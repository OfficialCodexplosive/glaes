import geokit as gk
from os.path import join, dirname, basename, isfile
from glob import glob
import re
import numpy as np
from collections import namedtuple, OrderedDict

from .priors import Priors, PriorSource
from .util import GlaesError

Areas = namedtuple('Areas', "coordinates geoms")

###############################
# Make an Exclusion Calculator
class ExclusionCalculator(object):
    """The ExclusionCalculator object makes land eligibility (LE) analyses easy and quick. Once initialized to a particular region, the ExclusionCalculator object can be used to incorporate any geospatial dataset (so long as it is interpretable by GDAL) into the LE analysis. 


    NOTE: By default, ExclusionCalculator is always initialized at 100x100 meter resolution in the EPSG3035 projection system. This is well suited to LE analyses in Europe, however if another region is being investigated or else if another resolution or projection system is desired for any other reason, this can be incorporated as well during the initialization stage.


    Initialization:
        * ExclusionCalculator can be initialized by passing a specific shapefile describing the investigation region:

            >>> ec = ExclusionCalculator(<path>)
        
        * Or a specific srs and resolution can be used:

            >>> ec = ExclusionCalculator(<path>, pixelSize=0.001, srs='latlon')

        * In fact, the ExclusionCalculator initialization is simply a call to geokit.RegionMask.load, so see that for more information. This also means that any geokit.RegoinMask object can be used to initialize the ExclusionCalculator

            >>> rm = geokit.RegionMask.load(<path>, pad=..., srs=..., pixelSize=..., ...)
            >>> ec = ExclusionCalculator(rm)

    Usage:
        * The ExclusionCalculator object contains a member name "availability", which contains the most up to date result of the LE analysis
            - Just after initialization, the the availability matrix is filled with 1's, meaning that all locations are available
            - After excluding locations based off various geospatial datasets, cells in the availability matrix are changed to a value between 0 and 1, where 0 means completely unavailable, 1 means fully available, and intermediate values indicate a pixel which is only partly excluded.
        * Exclusions can be applied by using one of the 'excludeVectorType', 'excludeRasterType', or 'excludePrior' methods
            - The correct method to use depends on the format of the datasource used for exclusions
        * After all exclusions have been applied...
            - The 'draw' method can be used to visualize the result
            - The 'save' method will save the result to a raster file on disc
            - The 'availability' member can be used to extract the availability matrix as a NumPy matrix for further usage
    """
    typicalExclusions = {
        "access_distance": (5000, None ),
        "agriculture_proximity": (None, 50 ),
        "agriculture_arable_proximity": (None, 50 ),
        "agriculture_pasture_proximity": (None, 50 ),
        "agriculture_permanent_crop_proximity": (None, 50 ),
        "agriculture_heterogeneous_proximity": (None, 50 ),
        "airfield_proximity": (None, 3000 ),
        "airport_proximity": (None, 5000 ),
        "connection_distance": (10000, None ),
        "dni_threshold": (None, 5.0 ),
        "elevation_threshold": (1800, None ),
        "ghi_threshold": (None, 5.0 ),
        "industrial_proximity": (None, 300 ),
        "lake_proximity": (None, 400 ),
        "mining_proximity": (None, 100 ),
        "ocean_proximity": (None, 1000 ),
        "power_line_proximity": (None, 200 ),
        "protected_biosphere_proximity": (None, 300 ),
        "protected_bird_proximity": (None, 1500 ),
        "protected_habitat_proximity": (None, 1500 ),
        "protected_landscape_proximity": (None, 500 ),
        "protected_natural_monument_proximity": (None, 1000 ),
        "protected_park_proximity": (None, 1000 ),
        "protected_reserve_proximity": (None, 500 ),
        "protected_wilderness_proximity": (None, 1000 ),
        "camping_proximity": (None, 1000), 
        "touristic_proximity": (None, 800),
        "leisure_proximity": (None, 1000),
        "railway_proximity": (None, 150 ),
        "river_proximity": (None, 200 ),
        "roads_proximity": (None, 150 ), 
        "roads_main_proximity": (None, 200 ),
        "roads_secondary_proximity": (None, 100 ),
        "sand_proximity": (None, 1000 ),
        "settlement_proximity": (None, 500 ),
        "settlement_urban_proximity": (None, 1000 ),
        "slope_threshold": (10, None ),
        "slope_north_facing_threshold": (3, None ),
        "wetland_proximity": (None, 1000 ),
        "waterbody_proximity": (None, 300 ),
        "windspeed_100m_threshold": (None, 4.5 ),
        "windspeed_50m_threshold": (None, 4.5 ),
        "woodland_proximity": (None, 300 ),
        "woodland_coniferous_proximity": (None, 300 ),
        "woodland_deciduous_proximity": (None, 300 ),
        "woodland_mixed_proximity": (None, 300 )}

    def __init__(s, region, **kwargs):
        """Initialize the ExclusionCalculator

        Inputs:
            region : The region definition for which land exclusions will be calculated
                - str : A path to a vector file containing the region definition
                - geokit.RegionMask : A preinitialized RegionMask object

            kwargs: 
                * All keyword arguments are passed on to a call to geokit.RegionMask.load
                * Most notably (most only operate when region is a path):
                    - 'srs' can be used to define the reference system to use
                    - 'pixelSize' can be used to define the resolution (in units of the srs)
                    - 'select' can be used to filter the vector source and extract a particular feature
        """

        # load the region
        s.region = gk.RegionMask.load(region, **kwargs)
        s.maskPixels = s.region.mask.sum()

        # Make the total availability matrix
        s._availability = np.array(s.region.mask)

        # Make a list of item coords
        s.itemCoords=None
    
    def save(s, output, **kwargs):
        """Save the current availability matrix to a raster file

        Inputs:
            output - str : The path of the output raster file

            kwargs: 
                * All keyword arguments are passed on to a call to geokit.RegionMask.createRaster
                * Most notably:
                    - 'noDataValue' is used to define the no data value
                    - 'dtype' is used to define the data type of the resulting raster
                    - 'overwrite' is used to force overwrite an existing file

        """
        s.region.createRaster(output=output, data=s.availability, **kwargs)

    def draw(s, ax=None, dataScaling=None, geomSimplify=None, output=None, noBorder=True, goodColor="#005b82", excludedColor="#8c0000"):
        """Draw the current availability matrix on a matplotlib figure

        Inputs:
            ax - matplotlib axis object : The axis to draw the figure onto
                * If given as 'None', then a fresh axis will be produced and displayed or saved immediately
                * When not 'None', then this function returns a handle to the drawn image which can be used however you see fit

            dataScaling - int : A down scaling factor to apply to the visualized matrix
                * Use this when visualizing a large area consumes too much memory

            geomSimplify - int : A down scaling factor to apply when drawing the geometry borders of the ExclusionCalculator's region
                * Use this when the region's geometry is extremely detailed compared to the scale over which it is drawn

            output - str : A path to save the output figure to
                * Only applies when 'ax' is None
                * If this is None and 'ax' is None, the figure is displayed immediately

            noBorder - T/F : A flag determining whether or not to show the borders of the plot's axis
                * Only useful when 'ax' is None

            goodColor : The color to apply to 'good' locations (having a value of 1)
                - str : An HTML color code, or any other string interpretable by matplotlib
                - (r,g,b) : Red, green, blue values given as a tuple
                    * Each must be between 0..1

            excludedColor : The color to apply to 'excluded' locations (having a value of 0)
                * See above for options
    
        """

        # import some things
        from matplotlib.colors import LinearSegmentedColormap
        
        # Do we need to make an axis?
        if ax is None:
            doShow = True
            # import some things
            import matplotlib.pyplot as plt

            # make a figure and axis
            plt.figure(figsize=(12,12))
            ax = plt.subplot(111)
        else: doShow=False

        # fix bad inputs
        if dataScaling: dataScaling = -1*abs(dataScaling)
        if geomSimplify: geomSimplify = abs(geomSimplify)

        # plot the region background
        s.region.drawGeometry(ax=ax, simplification=geomSimplify, fc=excludedColor, ec='None', zorder=0)

        # plot the availability
        a2b = LinearSegmentedColormap.from_list('alpha_to_blue',[(1,1,1,0),goodColor])
        gk.raster.drawImage(s.availability, bounds=s.region.extent, ax=ax, scaling=dataScaling, cmap=a2b)

        # Draw the region boundaries
        s.region.drawGeometry(ax=ax, simplification=geomSimplify, fc='None', ec='k', linewidth=3)

        # Draw Items?
        if not s.itemCoords is None:
            ax.plot(s.itemCoords[:,0], s.itemCoords[:,1], 'ok')

        # Done!
        if doShow:
            ax.set_aspect('equal')
            ax.autoscale(enable=True)

            if noBorder:
                plt.axis('off')

            if output: 
                plt.savefig(output, dpi=200)
                plt.close()
            else: 
                plt.show()
        else:
            return ax

    @property
    def availability(s): 
        """A matrix containing the availability of each location after all applied exclusions.
            * A value of 1 is interpreted as fully available
            * A value of 0 is interpreted as completely excluded
            * In between values are...in between"""
        return s._availability 

    @property
    def percentAvailable(s): 
        """The percent of the region which remains available"""
        return 100*s.availability.sum()/s.region.mask.sum()

    @property
    def areaAvailable(s): 
        """The area of the region which remains available
            * Units are defined by the srs used to initialize the ExclusionCalculator"""
        return s.availability.sum()*s.region.pixelWidth*s.region.pixelHeight

    ## General excluding functions
    def excludeRasterType(s, source, value=None, valueMin=None, valueMax=None, **kwargs):
        """Exclude areas based off the values in a raster datasource

        Inputs:
            source : The raster datasource defining the values for each location
                - str : A path to a raster data source
                - gdal Dataset : An open gdal dataset object held in memory
            
            value : The exact value, or value range to exclude
                - Numeric : The exact value to exclude
                    * Generally this should only be done when the raster datasource contains integer values, 
                      otherwise a range of values should be used to avoid float comparison errors
                - ( Numeric, Numeric ) : The low and high boundary describing the range of values to exclude
                    * If either boundary is given as None, then it is interpreted as unlimited

            valueMin - Numeric : A convenience input when the desired exclusion range is all values above 
                a minimal value
                * This is equivalent to value=(valueMin, None)

            valueMax - Numeric : A convenience input when the desired exclusion range is all values below 
                a maximal value
                * This is equivalent to value=(None, valueMax)

            kwargs
                * All other keyword arguments are passed on to a call to geokit.RegionMask.indicateValues
                * Most importantly...
                    - 'resampeAlg' is used to define how the indication matrix is warped to fit the region mask
                    - 'resolutionDiv' is used to increase the resolution of the working matrix during processing
                    - 'buffer' is used to add a buffer region (given in units of the ExclusionCalculator's srs) around the raw indicated areas
        """
        if value is None and valueMin is None and valueMax is None:
            raise GlaesError("One of value, valueMin, or valueMax must be given")

        # Indicate on the source
        if not (valueMin is None and valueMax is None): value = (valueMin,valueMax)
        areas = s.region.indicateValues(source, value, **kwargs)
        
        # exclude the indicated area from the total availability
        s._availability = np.min([s._availability, 1-areas],0)

    def excludeVectorType(s, source, where=None, invert=False, **kwargs):
        """Exclude areas based off the features in a vector datasource

        Inputs:
            source : The raster datasource defining the features to indicate from
                - str : A path to a vector data source
                - gdal Dataset : An open gdal dataset object held in memory
            
            where - str : A filtering statement to apply to the datasource before the initial indication
                * This is an SQL like statement which can operate on features in the datasource
                * For tips, see "http://www.gdal.org/ogr_sql.html"
                * For example...
                    - If the datasource had features which each have an attribute called 'type' and only features with the type "protected" are wanted, the correct statement would be: 
                        where="type='protected'"
            
            invert - T/F : Flag causing the exclusion of all unindicated areas, instead of all indicated areas 
            
            kwargs
                * All other keyword arguments are passed on to a call to geokit.RegionMask.indicateFeatures
                * Most importantly...
                    - 'resolutionDiv' is used to increase the resolution of the working matrix during processing
                    - 'buffer' is used to add a buffer region (given in units of the ExclusionCalculator's srs) around the raw indicated features
        """
        if isinstance(source, PriorSource):
            edgeI = kwargs.pop("edgeIndex", np.argwhere(source.edges==source.typicalExclusion))
            source = source.generateVectorFromEdge( s.region.extent, edgeIndex=edgeI )

        # Indicate on the source
        areas = s.region.indicateFeatures(source, where=where, **kwargs)
        
        # exclude the indicated area from the total availability
        if invert:
            s._availability = np.min([s._availability, areas],0)
        else:
            s._availability = np.min([s._availability, 1-areas],0)

    def excludePrior(s, prior, value=None, valueMin=None, valueMax=None, **kwargs):
        """Exclude areas based off the values in one of the Prior datasources

            * The Prior datasources are currently only defined over Europe
            * All Prior datasources are defined in the EPSG3035 projection system with 100x100 meter resolution
            * For each call to excludePrior, a temporary raster datasource is generated around the ExclusionCalculator's region, after which a call to ExclusionCalculator.excludeRasterType is made, therefore all the same inputs apply here as well

        Inputs:
            source - str : The name of the Prior datasource defining the values for each location
                * If the name does not exactly match one of the Prior datasources, the best fitting name will be used (and you will be informed about which one is chosen)
                * See the ExclusionCalculator.typicalExclusions dictionary for the Prior dataset names and what a typical exclusion threshold would be
                * A list of Prior datasets names can also be found in Priors.sources
            
            value : The exact value, or value range to exclude
                - Numeric : The exact value to exclude
                    * Generally this should only be done when the raster datasource contains integer values, 
                      otherwise a range of values should be used to avoid float comparison errors
                - ( Numeric, Numeric ) : The low and high boundary describing the range of values to exclude
                    * If either boundary is given as None, then it is interpreted as unlimited
                * If value, valueMin, and valueMax are all None, the typical exclusion threshold given from ExclusionCalculator.typicalExclusions is used

            valueMin - Numeric : A convenience input when the desired exclusion range is all values above 
                a minimal value
                * This is equivalent to value=(valueMin, None)

            valueMax - Numeric : A convenience input when the desired exclusion range is all values below 
                a maximal value
                * This is equivalent to value=(None, valueMax)

            kwargs
                * All other keyword arguments are passed on to a call to geokit.RegionMask.indicateValues
                * Most importantly...
                    - 'buffer' is used to add a buffer region (given in units of the ExclusionCalculator's srs) around the raw indicated areas
        """
        if not (valueMin is None and valueMax is None): value = (valueMin,valueMax)

        # make sure we have a Prior object
        if isinstance(prior, str): prior = Priors[prior]

        if not isinstance( prior, PriorSource): raise GlaesError("'prior' input must be a Prior object or an associated string")

        # try to get the default value if one isn't given
        if value is None:
            try:
                value = s.typicalExclusions[prior.displayName]
            except KeyError:
                raise GlaesError("Could not find a default exclusion set for %s"%prior.displayName)

        # Check the value input
        if isinstance(value, tuple):

            # Check the boundaries
            if not value[0] is None: prior.containsValue(value[0], True)
            if not value[1] is None: prior.containsValue(value[1], True)

            # Check edges
            if not value[0] is None: prior.valueOnEdge(value[0], True)
            if not value[1] is None: prior.valueOnEdge(value[1], True)
        else:
            if not value==0:
                print("WARNING: It is advisable to exclude by a value range instead of a singular value")    

        # Make the raster
        source = prior.generateRaster( s.region.extent )

        # Call the excluder
        s.excludeRasterType( source, value=value, **kwargs)

    def distributeItems(s, separation, pixelDivision=5, preprocessor=lambda x: x>=0.5, maxItems=10000000):
        """Distribute the maximal number of minimally separated items within the available areas
        
        Returns a list of x/y coordinates (in the ExclusionCalculator's srs) of each placed item

        Inputs:
            separation - float : The minimal distance between two items

            pixelDivision - int : The inter-pixel fidelity to use when deciding where items can be placed

            preprocessor : A preprocessing function to convert the accessibility matrix to boolean values
                - lambda function
                - function handle

            maxItems - int : The maximal number of items to place in the area
                * Used to initialize a placement list and prevent using too much memory when the number of placements gets absurd
        """
        # Preprocess availability
        workingAvailability = preprocessor(s.availability)
        if not workingAvailability.dtype == 'bool':
            raise s.GlaesError("Working availability must be boolean type")

        # Turn separation into pixel distances
        separation = separation / s.region.pixelSize
        sep2 = separation**2
        sepFloor = max(separation-1,0)
        sepFloor2 = sepFloor**2
        sepCeil = separation+1

        # Make geom list
        x = np.zeros((maxItems))
        y = np.zeros((maxItems))

        bot = 0
        cnt = 0

        # start searching
        yN, xN = workingAvailability.shape
        substeps = np.linspace(-0.5, 0.5, pixelDivision)
        substeps[0]+=0.0001 # add a tiny bit to the left/top edge (so that the point is definitely in the right pixel)
        substeps[-1]-=0.0001 # subtract a tiny bit to the right/bottom edge for the same reason
        
        for yi in range(yN):
            # update the "bottom" value
            tooFarBehind = yi-y[bot:cnt] > sepCeil # find only those values which have a y-component greater than the separation distance
            if tooFarBehind.size>0: 
                bot += np.argmin(tooFarBehind) # since tooFarBehind is boolean, argmin should get the first index where it is false

            #print("yi:", yi, "   BOT:", bot, "   COUNT:",cnt)

            for xi in np.argwhere(workingAvailability[yi,:]):
                # Clip the total placement arrays
                xClip = x[bot:cnt]
                yClip = y[bot:cnt]

                # calculate distances
                xDist = np.abs(xClip-xi)
                yDist = np.abs(yClip-yi)

                # Get the indicies in the possible range
                possiblyInRange = np.argwhere( xDist <= sepCeil ) # all y values should already be within the sepCeil 

                # only continue if there are no points in the immediate range of the whole pixel
                immidiateRange = (xDist[possiblyInRange]*xDist[possiblyInRange]) + (yDist[possiblyInRange]*yDist[possiblyInRange]) <= sepFloor2
                if immidiateRange.any(): continue

                # Start searching in the 'sub pixel'
                found = False
                for xsp in substeps+xi:
                    xSubDist = np.abs(xClip[possiblyInRange]-xsp)
                    for ysp in substeps+yi:
                        ySubDist = np.abs(yClip[possiblyInRange]-ysp)

                        # Test if any points in the range are overlapping
                        overlapping = (xSubDist*xSubDist + ySubDist*ySubDist) <= sep2
                        if not overlapping.any():
                            found = True
                            break

                    if found: break

                # Add if found
                if found:
                    x[cnt] = xsp
                    y[cnt] = ysp
                    cnt += 1
                 
        # Convert identified points back into the region's coordinates
        coords = np.zeros((cnt,2))
        coords[:,0] = s.region.extent.xMin + (x[:cnt]+0.5)*s.region.pixelWidth # shifted by 0.5 so that index corresponds to the center of the pixel
        coords[:,1] = s.region.extent.yMax - (y[:cnt]+0.5)*s.region.pixelHeight # shifted by 0.5 so that index corresponds to the center of the pixel

        # Done!
        s.itemCoords = coords
        return coords
    
    def distributeAreas(s, targetArea=2000000, preprocessor=lambda x: x>=0.5):
        """Distribute the maximal number of roughly equal sized partitions within the available areas
        
        Returns a list of tuples, each containing x/y coordinates (in the ExclusionCalculator's srs) of a partition as well as the partition's geometry

        Inputs:
            targetArea - float : The desired area (in units of the ExclusionCalculator's srs) for each partition

            preprocessor : A preprocessing function to convert the accessibility matrix to boolean values
                - lambda function
                - function handle
        """
        # Get the working availability
        workingAvailability = preprocessor(s.availability)
        if not workingAvailability.dtype == 'bool':
            raise s.GlaesError("Working availability must be boolean type")

        # polygonize availability
        geoms, values = gk.raster.polygonize(workingAvailability, bounds=s.region.extent, noDataValue=0, shrink=True)
        
        # partition each of the new geometries
        newGeoms = []
        for gi in range(0, len(geoms)):
            g = geoms[gi]
            
            gArea = g.Area()
            if gArea <= targetArea*1.6:
                newGeoms.append(g.Clone())
                
            else:
                #gTmp, vTmp = gk.geom.partitionArea(g, targetArea=fudgedTargetArea, resolution=s.region.pixelSize, **kwargs)
                try:
                    gTmp = gk.geom.partition(g, targetArea=targetArea, growStep=s.region.pixelSize*3)
                except Exception as e:
                    print(gi)
                    raise e 

                newGeoms.extend(gTmp)
                
        # finalize geom list
        def inRange(x):
            area = x.Area()
            if area >= (0.5*targetArea) and area <= (2.1 * targetArea):
                return True
            else:
                return False
        newGeoms = np.array(list(filter(inRange, newGeoms)))

        # get centroids and return
        return Areas( np.array([g.Centroid().GetPoints() for g in newGeoms]), newGeoms)


class WeightedCriterionCalculator(object):
    typicalValueScores = {
        # THESE NEED TO BE CHECKED!!!!
        "access_distance": ((0,1), (5000,0.5), (20000,0), ),
        "agriculture_proximity": ((0,0), (100,0.5), (1000,1), ),
        "agriculture_arable_proximity": ((0,0), (100,0.5), (1000,1), ),
        "agriculture_pasture_proximity": ((0,0), (100,0.5), (1000,1), ),
        "agriculture_permanent_crop_proximity": ((0,0), (100,0.5), (1000,1), ),
        "agriculture_heterogeneous_proximity": ((0,0), (100,0.5), (1000,1), ),
        "airfield_proximity": ((0,0), (3000,0.5), (8000,1), ),
        "airport_proximity": ((0,0), (4000,0.5), (10000,1), ),
        "connection_distance": ((0,1), (10000,0.5), (20000,0), ),
        "dni_threshold": ((0,0), (4,0.5), (8,1), ),
        "elevation_threshold": ((1500,1), (1750,0.5), (2000,0), ),
        "ghi_threshold": ((0,0), (4,0.5), (8,1), ),
        "industrial_proximity": ((0,0), (300,0.5), (1000,1), ),
        "lake_proximity": ((0,0), (300,0.5), (1000,1), ),
        "mining_proximity": ((0,0), (200,0.5), (1000,1), ),
        "ocean_proximity": ((0,0), (300,0.5), (1000,1), ),
        "power_line_proximity": ((0,0), (150,0.5), (500,1), ),
        "protected_biosphere_proximity": ((0,0), (1000,0.5), (2000,1), ),
        "protected_bird_proximity": ((0,0), (1000,0.5), (2000,1), ),
        "protected_habitat_proximity": ((0,0), (1000,0.5), (2000,1), ),
        "protected_landscape_proximity": ((0,0), (1000,0.5), (2000,1), ),
        "protected_natural_monument_proximity": ((0,0), (1000,0.5), (2000,1), ),
        "protected_park_proximity": ((0,0), (1000,0.5), (2000,1), ),
        "protected_reserve_proximity": ((0,0), (1000,0.5), (2000,1), ),
        "protected_wilderness_proximity": ((0,0), (1000,0.5), (2000,1), ),
        "railway_proximity": ((0,0), (200,0.5), (1000,1), ),
        "river_proximity": ((0,0), (400,0.5), (1000,1), ),
        "roads_proximity": ((0,0), (200,0.5), (1000,1), ),
        "roads_main_proximity": ((0,0), (200,0.5), (1000,1), ),
        "roads_secondary_proximity": ((0,0), (100,0.5), (1000,1), ),
        "settlement_proximity": ((0,0), (700,0.5), (2000,1), ),
        "settlement_urban_proximity": ((0,0), (1500,0.5), (3000,1), ),
        "slope_threshold": ((0,1), (11,0.5), (20,0), ),
        "slope_north_facing_threshold": ((0,1), (3,0.5), (5,0), ),
        "waterbody_proximity": ((0,0), (400,0.5), (1000,1), ),
        "wetland_proximity": ((0,0), (200,0.5), (1000,1), ),
        "windspeed_100m_threshold": ((0,0), (5,0.5), (8,1), ),
        "windspeed_50m_threshold": ((0,0), (5,0.5), (8,1), ),
        "woodland_coniferous_proximity": ((0,0), (300,0.5), (1000,1), ),
        "woodland_deciduous_proximity": ((0,0), (300,0.5), (1000,1), ),
        "woodland_mixed_proximity": ((0,0), (300,0.5), (1000,1), )}

    def __init__(s, region, exclusions=None, **kwargs):

        # load the region
        s.region = gk.RegionMask.load(region, **kwargs)
        s.maskPixels = s.region.mask.sum()

        # Make the total availability matrix
        s._unnormalizedWeights = OrderedDict()
        s._totalWeight = 0
        s._result = None
        s.noData = -1

        # Keep an exclusion matrix
        if not exclusions is None:
            if not exclusions.dtype == np.bool:
                raise GlaesError("Exclusion matrix must be a boolean type")
            if not exclusions.shape == s.region.mask.shape:
                raise GlaesError("Exclusion matrix shape must match the region's mask shape")
            s.exclusions = exclusions
        else:
            s.exclusions = None
    
    def save(s, output, **kwargs):
        s.region.createRaster(output=output, data=s.result, **kwargs)

    def draw(s, ax=None, dataScaling=None, geomSimplify=None, output=None, view='local'):
        # import some things
        from matplotlib.colors import LinearSegmentedColormap
        
        # Do we need to make an axis?
        if ax is None:
            doShow = True
            # import some things
            import matplotlib.pyplot as plt

            # make a figure and axis
            plt.figure(figsize=(12,12))
            ax = plt.subplot(111)
        else: doShow=False

        # fix bad inputs
        if dataScaling: dataScaling = -1*abs(dataScaling)
        if geomSimplify: geomSimplify = abs(geomSimplify)

        # plot the region background
        s.region.drawGeometry(ax=ax, simplification=geomSimplify, fc=(140/255,0,0), ec='None', zorder=0)

        # plot the result
        rbg = LinearSegmentedColormap.from_list('blue_green_red',[(130/255,0,0,1), (0,91/255, 130/255, 1), (0,130/255, 0, 1)])
        rbg.set_under(color='w',alpha=1)

        if view == 'local': result = s.resultLocal
        elif view == 'global': result = s.resultGlobal
        elif view == 'raw': result = s.resultRaw
        else: raise GlaesError("view not understood")

        h = gk.raster.drawImage(result, bounds=s.region.extent, ax=ax, scaling=dataScaling, cmap=rbg, vmin=0)

        # Draw the region boundaries
        s.region.drawGeometry(ax=ax, simplification=geomSimplify, fc='None', ec='k', linewidth=3)

        # Done!
        if doShow:
            plt.colorbar(h)
            ax.set_aspect('equal')
            ax.autoscale(enable=True)
            if output: 
                plt.savefig(output, dpi=200)
                plt.close()
            else: 
                plt.show()
        else:
            return ax

    @property
    def resultLocal(s):
        """The pixelated areas left over after all weighted overlays"""
        minV = s.result[s.region.mask].min()
        maxV = s.result[s.region.mask].max()
        out = (s.result-minV)/(maxV-minV)
        return s.region.applyMask( out, noData=s.noData)

    @property
    def resultGlobal(s):
        """The pixelated areas left over after all weighted overlays"""
        out = s.result/s.totalWeight
        return s.region.applyMask( out, noData=s.noData)

    @property
    def resultRaw(s):
        """The pixelated areas left over after all weighted overlays"""
        return s.region.applyMask( s.result, noData=s.noData)

    @property
    def totalWeight(s): 
        """The pixelated areas left over after all applied exclusions"""
        return s._totalWeight

    @property
    def result(s): 
        """The pixelated areas left over after all applied exclusions"""
        if s._result is None: s.combine()
        return s._result

    ## General excluding function
    def addCriterion(s, source, vs=None, name=None, weight=1, resampleAlg='cubic', **kwargs):
        """Exclude areas as calcuclated by one of the indicator functions in glaes.indicators

        * if not 'value' input is given, the default buffer/threshold value is chosen (see the individual function's 
          docstring for more information)
        """
        if isinstance(source, str): source = Priors[source]
        if isinstance(source, PriorSource):
            name = source.displayName if name is None else name
            
            if vs is None:
                vs = s.typicalValueScores[source.displayName]
                
            source = source.generateRaster( s.region.extent)

            skipClip=True # we dont need to clip again

        else: 
            skipClip=False # we will likely still need to clip
            if name is None: 
                if isinstance(source, str):
                    name=basename(source)
                else:
                    raise GlaesError("A 'name' input must be provided when source is not a prior or a path")

        # make sure known inputs are okay
        knownValues = [x[0] for x in vs]
        knownScores = [x[1] for x in vs]

        if knownValues[0] > knownValues[-1]: # if known values are given in DESCENDING order, flip both
            knownValues = knownValues[::-1]
            knownScores = knownScores[::-1]

        # make a mutator
        def mutator(data):
            return np.interp(data, knownValues, knownScores)

        # mclip,mutate,andwarp the datasource
        clippedDS = source if skipClip else s.region.extent.clipRaster(source)
        mutDS = gk.raster.mutateValues( clippedDS, processor=mutator)
        result = s.region.warp(mutDS, resampleAlg=resampleAlg)

        newWeights = weight*result
        s._totalWeight += weight

        # append
        s._unnormalizedWeights[name] = newWeights

        # make sure to clear any old result
        s._result = None

    def combine(s, combiner='sum'):
        # Set combiner
        if combiner == 'sum':
            result = np.zeros(s.region.mask.shape)
            for k,v in s._unnormalizedWeights.items(): result+=v
        
        elif combiner == 'mult':
            result = np.ones(s.region.mask.shape)
            for k,v in s._unnormalizedWeights.items(): result*=v
        
        else:
            result = combiner(s._unnormalizedWeights)

        # apply mask if one exists
        if not s.exclusions is None:
            result *= s.exclusions

        # do combination
        s._result = result

    def extractValues(s, locations, view='local', srs=None, mode='linear-spline', **kwargs):
        # get result 
        if view == 'local': result = s.resultLocal
        elif view == 'global': result = s.resultGlobal
        elif view == 'raw': result = s.resultRaw
        else: raise GlaesError("view not understood")

        # Fill no data
        if not mode=='near': # there's no point if we're using 'near'
            result[ result==s.noData ] = 0

        # make result into a dataset
        ds = s.region.createRaster(data=result)

        # make sure we have an srs
        if srs is None: srs = s.region.srs
        else: srs = gk.srs.loadSRS(srs)

        # extract values
        vals = gk.raster.interpolateValues(ds, locations, pointSRS=srs, mode=mode, **kwargs)

        # done!
        return vals