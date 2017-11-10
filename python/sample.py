"""
 **********************************************************************************
 * Project: HistFitter - A ROOT-based package for statistical data analysis       *
 * Package: HistFitter                                                            *
 * Class  : Sample                                                                *
 * Created: November 2012                                                         *
 *                                                                                *
 * Description:                                                                   *
 *      Class to define a sample                                                  *
 *                                                                                *
 * Authors:                                                                       *
 *      HistFitter group, CERN, Geneva                                            *
 *                                                                                *
 * Redistribution and use in source and binary forms, with or without             *
 * modification, are permitted according to the terms listed in the file          *
 * LICENSE.                                                                       *
 **********************************************************************************
"""

import ROOT
from ROOT import TFile, TMath, RooRandom, TH1, TH1F
from ROOT import kBlack, kWhite, kGray, kRed, kPink, kMagenta, kViolet, kBlue, kAzure, kCyan, kTeal, kGreen, kSpring, kYellow, kOrange, kDashed, kSolid, kDotted
from math import fabs
from logger import Logger
from systematic import SystematicBase
from inputTree import InputTree

log = Logger('Sample')

TH1.SetDefaultSumw2(True)

from copy import deepcopy
from configManager import configMgr, replaceSymbols

def chi2test(h1, h2):
    if h2.Integral() == 0:
        return 1
    norm = h1.Integral() / h2.Integral()

    from scipy.stats import chi2

    binsX = xrange(1, h1.GetNbinsX()+1)
    binsY = xrange(1, h1.GetNbinsY()+1) if h1.InheritsFrom("TH2") else [0]
    
    test_chi2, dof = 0, 0
    for i in binsX:
        for j in binsY:
            idx = h1.GetBin(i, j)

            if(h1.GetBinContent(idx) * h1.GetBinContent(idx) == 0 and h2.GetBinContent(idx) * h2.GetBinContent(idx)):
                continue

            sigma = max([h1.GetBinError(idx), h2.GetBinError(idx)*norm])
            if sigma == 0: continue
            test_chi2 += ((h1.GetBinContent(idx) - h2.GetBinContent(idx)*norm) / sigma)**2
            dof += 1


    #print "dof = {}".format(dof)
    #print "test_chi2 = {}".format(test_chi2)

    #print ROOT.TMath.Prob(test_chi2, dof)

    return chi2.sf(test_chi2, dof)

def checkNormalizationEffect(hNom, hUp, hDown, norm_threshold=0.005):
    # True for keeping norm effect, false for pruning
    nom_integral = hNom.Integral()
    
    if nom_integral == 0:
        return True
    
    up_norm = hUp.Integral() / nom_integral
    down_norm = hDown.Integral() / nom_integral
   
    max_variation = max([abs(up_norm-1), abs(down_norm-1)])
    if max_variation < norm_threshold:
        log.verbose("checkNormalizationEffect(): {}, {}, {}: max variation = {:.3f}, threshold = {:.3f}".format(hNom.GetName(), hUp.GetName(), hDown.GetName(), max_variation, threshold))
        return False

    return True
    
def checkShapeEffect(hNom, hUp, hDown, chi2_threshold=0.95, use_overflows=True):
    # Perform a weighted comparison including the overflow and underflow, unless the user says they don't want it
    opt = "WW OF UF"
    if not use_overflows:
        opt = "WW"

    # ROOT prints annoying messages about bin content at Info level; suppress them temporarily
    _err_level = ROOT.gErrorIgnoreLevel
    ROOT.gErrorIgnoreLevel = 2000

    up_pvalue = hNom.Chi2Test(hUp, opt)
    down_pvalue = hNom.Chi2Test(hDown, opt)
    
    ROOT.gErrorIgnoreLevel = _err_level
    
    #up_pvalue = chi2test(hNom, hUp)
    #down_pvalue = chi2test(hNom, hDown)
    
    pvalue = min([up_pvalue, down_pvalue])
    if not pvalue < chi2_threshold:
        log.verbose("checkShapeEffect(): {}, {}, {}: up_pvalue = {:.3f}, down_pvalue = {:3f}, chi2 threshold = {:.3f}".format(hNom.GetName(), hUp.GetName(), hDown.GetName(), up_pvalue, down_pvalue, chi2_threshold))
        return False

    return True

def symmetrizeSystematicEnvelope(nomName, lowName, highName):
    # Loop over all bins - and look for the biggest error
    for iBin in xrange(1, configMgr.hists[lowName].GetNbinsX()+1):
        lowVal = configMgr.hists[lowName].GetBinContent(iBin)
        highVal = configMgr.hists[highName].GetBinContent(iBin)
        nomVal = configMgr.hists[nomName].GetBinContent(iBin)
        
        lowErr = fabs(nomVal-lowVal)
        highErr = fabs(highVal-nomVal)

        err = max(lowErr, highErr)

        # If low' = (nominal-error) is < 0, truncate it to 0
        newLowVal = (nomVal - err)
        if newLowVal < 0.0:
            log.warning("symmetrizeSystematicEnvelope(): low={0:f} is < 0.0 in {1:s} bin {2:d}. Setting negative bins to 0.0.".format(newLowVal, lowName, iBin))
        newHighVal = nomVal + err
        
        log.debug("symmetrizeSystematicEnvelope(): bin {0:d} -> found nom={1}, low={2}, high={3} => symmetrized error to low={4} high={5}".format(iBin, nomVal, lowVal, highVal, newLowVal, newHighVal))

        configMgr.hists[highName].SetBinContent(iBin, newHighVal) 
        configMgr.hists[lowName].SetBinContent(iBin, newLowVal) 

    return

class Sample(object):
    """
    Defines a Sample belonging to a Channel
    """

    def __init__(self, name, color=1):
        """
        Store configuration, set sample name, and if to normalize by theory

        Scales histograms to luminosity set in configuration

        @param name Name of the sample
        @param colour Colour of the sample used in before/after plotting
        """
        
        ## Name of the sample
        self.name = name
        ## Colour used in before/after fit plots
        self.color = color 
        ## Flag indicating whether the sample is data
        self.isData = False
        ## Flag indicating whether the sample is QCD
        self.isQCD = False
        ## Flag indicating whether the sample is a discovery sample
        self.isDiscovery = False
        self.write = True
        ## Normalise the sample to various regions or not
        self.normByTheory = False
        ## Use HistFactory statConfig for the channel
        self.statConfig = False
        ## Internal list of histogram-based systematics
        self.histoSystList = []
        ## Internal list of shape systematics
        self.shapeSystList = []
        ## Internal list of overall systematics
        self.overallSystList = []
        ## Internal list of shape factors
        self.shapeFactorList = []
        ## Internal list of all systematics
        self.systList = []
        ## Internal list of weights
        self.weights = []
        ## Internal list of sample-specific weights
        self.tempWeights = []
        ## Internal dictionary of systematics
        self.systDict = {}
        ## Flag for the current systematic - needs to be a key of the dict above, or None
        self.currentSystematic = None
        ## Internal list of normalisation factors
        self.normFactor = []
        self.qcdSyst = None
        ## Units used for the sample
        self.unit = "GeV"
        ## Dictionary of cuts placed on the sample in various regions
        self.cutsDict = {}
        ## List of input files - combinations have to be unique
        self.input_files = set()
        ## Override for input tree name
        self.overrideTreename = ""
        ## Prefix of input tree
        self.prefixTreeName = "" 
        ## Suffix of input tree
        self.suffixTreeName = ""   
        ## Name of a friends tree (in the same files) to add
        self.friendTreeName = ""
        ## Additional selection applied on this sample
        self.additionalCuts = ""
        ## Nominal cross-section weight for signal samples
        self.xsecWeight = None
        ## +1 sigma variation of cross-section weight
        self.xsecUp = None
        ## -1 sigma variation of cross-section weight
        self.xsecDown = None
        ## List of regions to normalise the samples to
        self.normRegions = None
        ## Remap sample to another one in normalisation
        self.normSampleRemap = ''
        self.noRenormSys = True
        self.parentChannel = None
        self.allowRemapOfSyst = True
        self.mergeOverallSysSet = []

        # will this sample be merged with something?
        self.toBeMerged = False

        if self.name[0].isdigit():
            log.warning("Sample name %s starts with a digit - this can confuse HistFactory internals" % self.name)

    def buildHisto(self, binValues, region, var, binLow=0.5, binWidth=1.):
        """
        Allow user to give bin values eg. for checking stats in papers

        @param binValues Values in the bins
        @param region Region to add the histogram to 
        @param var The variable to bin in
        @param binLow Lower bin edge (default 0.5)
        @param binWidth Widths of the bins (default 1.)	
        """
        try:
            self.binValues[(region, var)] = binValues
        except AttributeError:
            self.binValues = {}
            self.binValues[(region, var)] = binValues

        if not self.isData:
            self.histoName = "h"+self.name+"Nom_"+region+"_obs_"+var
        else:
            self.histoName = "h"+self.name+"_"+region+"_obs_"+var

        configMgr.hists[self.histoName] = TH1F(self.histoName, self.histoName, len(self.binValues[(region, var)]), binLow, float(len(self.binValues[(region, var)]))*binWidth+binLow)
        for (iBin, val) in enumerate(self.binValues[(region, var)]):
            configMgr.hists[self.histoName].SetBinContent(iBin+1, val)

        return

    def buildStatErrors(self, binStatErrors, region, var):
        """
        Allow user to give bin stat errors eg. for checking stats in papers
        
        @param binStatErrors Statistical errors for the bins
        @param region Region to add the errors to
        @param var The variable the region is binned in; 'cuts' for a cut-and-count analysis
        """
        try:
            self.binStatErrors[(region, var)] = binStatErrors
        except AttributeError:
            self.binStatErrors = {}
            self.binStatErrors[(region, var)] = binStatErrors

        if not len(self.binStatErrors[(region, var)]) == len(self.binValues[(region, var)]):
            raise Exception("Length of errors list in region %s and variable %s does not match the nominal histogram!" % (region, var))

        if not self.isData:
            self.histoName = "h"+self.name+"Nom_"+region+"_obs_"+var
        else:
            self.histoName = "h"+self.name+"_"+region+"_obs_"+var

        for (iBin, err) in enumerate(self.binStatErrors[(region, var)]):
            try:
                configMgr.hists[self.histoName].SetBinError(iBin+1, err)
            except:
                raise Exception("Errors specified without building histogram!")

    def Clone(self):
        """
        Copy a the sample into a new instance
        """
        newInst = deepcopy(self)
        #for (key, val) in self.systDict.items():
        #    newInst.systDict[key] = val
        return newInst

    def setUnit(self, unit):
        """
        Set the units units for this sample

        @param unit String representing the unit
        """
        self.unit = unit
        return

    def setCutsDict(self, cutsDict):
        """
        Set cuts dictionary for the sample

        @param cutsDict A dictionary of regions to cuts
        """
        self.cutsDict = cutsDict
        return

    def setData(self, isData=True):
        """
        Flag the sample as a data sample

        @param isData A boolean indicating whether the sample contains data or not
        """
        self.isData = isData
        return

    def setWeights(self, weights):
        """
        Set the weights for this sample - overrides
        
        @param weights List of weights to set
        """
        self.weights = deepcopy(weights)
        return

    def addSampleSpecificWeight(self, weight):
        """
        Add a sample-specific weight to this sample

        @param weight The weight to append to the list of weights
        """
        if not weight in self.tempWeights:
            self.tempWeights.append(weight)
            ## MB : propagated to actual weights in configManager, after all
            ##      systematics have been added
        else:
            raise RuntimeError("Weight %s already defined for sample %s" % (weight, self.name))

    def addWeight(self, weight):
        """
        Add a weight to this sample and propagate

        @param weight The weight to append ot the various lists of weights. High/low values will be ignored if already present; if the nominal value is present, a RunTimeError is thrown.
        """
        if not weight in self.weights:
            self.weights.append(weight)
        else:
            raise RuntimeError("Weight %s already defined in sample %s" % (weight, self.name))

        for syst in self.systDict.values():
            if syst.type == "weight":
                if not weight in syst.high:
                    syst.high.append(weight)
                if not weight in syst.low:
                    syst.low.append(weight)
        return

    def removeWeight(self, weight):
        """
        Remove a weight from the sample and the associated systematics

        @param weight The weight to remove
        """
        if weight in self.weights:
            self.weights.remove(weight)
        for syst in self.systDict.values():
            if syst.type == "weight":
                if weight in syst.high:
                    syst.high.remove(weight)
                if weight in syst.low:
                    syst.low.remove(weight)
        return
    
    def setQCD(self, isQCD=True, qcdSyst="uncorr"):
        """
        Set a flag that the sample is QCD

        @param isQCD A boolean
        @param qcdSyst A string to indicate the systematic
        """
        self.isQCD = isQCD
        self.qcdSyst = qcdSyst
        return

    def setDiscovery(self, isDiscovery=True):
        """
        Flag the sample as a discovery sample

        @param isDiscovery Boolean to set (default True)
        """
        self.isDiscovery = isDiscovery
        return

    def setNormByTheory(self, normByTheory=True):
        """
        Flag the sample as normalised by the luminosity

        @param normByTheory Boolean to set (default True)
        """
        self.normByTheory = normByTheory
        return

    def setStatConfig(self, statConfig):
        """
        Set the stat configuration for this sample (see HistFactory documentation)

        @param statConfig String to indicate the configuration 
        """
        self.statConfig = statConfig
        return

    def setWrite(self, write=True):
        self.write = write
        return

    def setHistoName(self, histoName):
        """
        Set the name of the nominal histogram for this sample
        
        @param histoName Name of the histogram
        """
        log.verbose("Setting histoName to {}".format(histoName))
        self.histoName = histoName
        return

    #def setTreeName(self, treeName):
        #"""
        #Set the tree name used for this sample

        #@param treeName Name of the tree
        #"""
        #self.treeName = treeName
        #return
    
    def setPrefixTreeName(self, prefixTreeName):
        """
        Set the prefix contained in every name of trees used for this sample - do not use setPreFixTreeName and setTreeName together. setTreeName will take precedence.

        @param prefixTreeName Name of the tree
        """
        self.prefixTreeName = prefixTreeName
        return  
    
    def setSuffixTreeName(self, suffixTreeName):
        """
        Set the suffix contained in every name of trees used for this sample - do not use setSuffixTreeName and setTreeName together. setTreeName will take precedence.

        @param suffixTreeName Name of the tree
        """
        self.suffixTreeName = suffixTreeName
        return     

    def setNormRegions(self, normRegions):
        """
        Normalise the sample in various regions

        @param normRegions A list of regions used to constrain the sample normalisation
        """
        self.normRegions = normRegions
        self.noRenormSys = False
        return

    def isBlinded(self, fitConfig):
        """
        Is the sample blinded in this fit config? 

        @param fitConfig The fit configuration to pass
        """

        if not self.isData:
            # non data is never blinded
            return False

        # is the channel we belong to blinded? then yes, otherwise no
        if self.parentChannel.isBlinded(fitConfig):
            return True

        return False

    def getAllHistogramNamesForSystematics(self, fitConfig):
        """
        Generate all names for systematic variations for this sample"

        @param fitConfig A fit configuration to pass
        @returns A generator to be used in loops
        """
        for name in self.systDict:
            syst = self.systDict[name]
            for var in ["Nom", "High", "Low"]:
                #retval.append(self.getHistogramName(fitConfig, syst.name, var))
                yield self.getHistogramName(fitConfig, syst.name, var)

            if syst.merged:
                mergedName = "".join(syst.sampleList)
                yield self.getHistogramName(fitConfig, mergedName) 
                for var in ["Nom", "High", "Low"]:
                    yield self.getHistogramName(fitConfig, mergedName, var) 
    
        #return retval

    def getHistogramName(self, fitConfig, syst_name="", variation=""):
        """
        Return the histogram name for with a possible variation

        @param The fit config to generate for (needed for blinded data)
        @param variation A variation: either the empty string (equivalent to Nom), or High (or Up) or Low (or Down)
        """

        if self.isData and variation != "":
            raise ValueError("Sample {}: is data, cannot specify variation!".format(self.name))

        # Special treatment for blinded samples
        if self.isBlinded(fitConfig):
            return "h{}{}Blind_{}_obs".format(fitConfig.name, self.name, "".join(self.parentChannel.regions), replaceSymbols(self.parentChannel.variableName))
       
        # Special treatment for data
        if self.isData:
            return "h{}_{}_obs_{}".format(self.name, "".join(self.parentChannel.regions), replaceSymbols(self.parentChannel.variableName))

        # Now on to the usual variation
        if variation == "":
            variation = "Nom"

        if variation == "Up":
            variation = "High"

        if variation == "Down":
            variation = "Low"

        variations = ["Nom", "High", "Low"]

        if not variation in variations:
            raise ValueError("Sample {}: cannot generate histogram name for unknown variation {}".format(variation))

        return "h{}{}{}_{}_obs_{}".format(self.name, syst_name, variation, "".join(self.parentChannel.regions), replaceSymbols(self.parentChannel.variableName))

    #def propagateTreeName(self, treeName):
        #"""
        #Propagate the tree name

        #@param treeName The tree name to set and propagate down
        #"""
        #if self.treeName == '':
            #self.treeName = treeName
        ## MAB: Propagate treeName down to systematics of sample
        ##for (systName, systList) in self.systDict.items():
           ##for syst in systList:
               ##syst.propagateTreeName(self.treeName)
               ##pass
        #return
   
    def setOverrideTreename(self, name):
        self.overrideTreename = name

    def removeCurrentSystematic(self):
        self.currentSystematic = None
   
    def setCurrentSystematic(self, name, mode="nominal"):
        if name is None:
            self.removeCurrentSystematic()
            return
        
        _name = name
        if isinstance(name, SystematicBase):
            _name = name.name
            
            if name.type == "weight":
                log.verbose("setCurrentSystematic: sample {}, name {} is a weight - not setting suffix".format(self.name, _name))
                return

        log.verbose("setCurrentSystematic: sample {}, name {}".format(self.name, _name))
        
        if _name is not None and _name not in self.systDict:
            raise ValueError("Sample {}: cannot set systematic to unknown {}".format(self.name, _name))

        if mode.lower() == "high" or mode.lower() == "up":
            self.currentSystematic = self.systDict[_name].high
            return

        if mode.lower() == "low" or mode.lower() == "down":
            self.currentSystematic = self.systDict[_name].low
            return

        self.currentSystematic = self.systDict[_name].nominal

    def getTreenameSuffix(self):
        if self.suffixTreeName != "":
            # no defaults if we're overruled
            return self.suffixTreeName
        
        if not self.isData and not self.isQCD and not self.isDiscovery:

            # are we in a systematic? if so, return that suffix
            if self.currentSystematic is not None:
                return self.currentSystematic

            # if we're not data, pick up the default
            return configMgr.nomName

        return ""

    def getTreename(self, suffix=""):
        """
        Get name of the tree to take histograms from
        """
        if self.overrideTreename != "":
            log.debug("Overriding treename for {} to {}".format(self.name, self.overrideTreename))
            return self.overrideTreename

        if self.prefixTreeName == "":
            self.prefixTreeName = self.name
            log.debug("Using name of sample as prefix for names of trees")
            
        _suffix = ""
        if suffix != "":
            _suffix = copy(suffix)
        else:
            _suffix = self.getTreenameSuffix()

        if _suffix != "":
            log.debug("Using tree suffix {}".format(_suffix))

        name = "{}{}".format(self.prefixTreeName, _suffix)

        log.debug("Using {} as tree name".format(name))

        return name

    # NOTE: not using @property because of the optional argument
    treename = property(getTreename)

    def addHistoSys(self, systName, nomName, highName, lowName, includeOverallSys, normalizeSys, symmetrize=False, oneSide=False, samName="", normString="", nomSysName="", symmetrizeEnvelope=False):
        """
        Add a HistoSys entry using the nominal, high and low histograms, set if to include OverallSys

        If includeOverallSys then extract scale factors

        If normalizeSys then normalize shapes to nominal

        @param systName Name of the systematic
        @param nomName Nominal name for the systematic
        @param highName Name of the +1sigma systematic value
        @param lowName Name of the -1sigma systematic value
        @param includeOverallSys Include an overallSys for the systematic uncertainty
        @param normalizeSys Normalize the systematic to the normRegions set through setNormRegions()
        @param symmetrize Boolean to indicate whether the low value has to be taken from the high value (default false)
        @param oneSide Boolean to indicate whether the uncertainty is one-sided (default False)
        @param samName Name of the sample
        @param normString String to append to the name of renormalised samples (default empty)
        @param nomSysName Name of the nominal systematic to generate high/low from (optional use (see source); default empty)
        @param symmetrizeEnvelope Boolean to indicate whether or not the envelope of up/down is taken as a symmetrical error
        """

        log.debug("addHistoSys(): building histograms {0} / {1} / {2}".format(nomName, highName, lowName))
        log.verbose("Using settings: includeOverallSys={0}, normalizeSys={1}, symmetrize={2}, oneSide={3}, symmetrizeEnvelope={4}".format(includeOverallSys, normalizeSys, symmetrize, oneSide, symmetrizeEnvelope)) 

        if oneSide and symmetrizeEnvelope:
            log.fatal("Cannot use oneSided histogram with symmetrizeEnvelope - use either, not both. Please check the systematic type of {0}".format(nomName))

        ### use-case of different tree from nominal histogram in case of 
        if len(nomSysName) > 0:
            if configMgr.hists[nomSysName] != None:
                configMgr.hists[lowName+"_test"] = configMgr.hists[lowName].Clone(lowName+"_test")
                log.info(lowName + " / " + nomSysName)
                success = configMgr.hists[lowName].Divide( configMgr.hists[nomSysName] )
                if not success:
                    log.error( "Can not divide: " + lowName + " by " + nomSysName )
                    raise RuntimeError("Divide by zero.")
                else:
                    log.info(lowName + " * " + nomName)
                    configMgr.hists[lowName].Multiply( configMgr.hists[nomName] )
                    pass
                #
                configMgr.hists[highName+"_test"] = configMgr.hists[highName].Clone(highName+"_test")
                log.info(highName + " * " + nomSysName)
                success = configMgr.hists[highName].Divide( configMgr.hists[nomSysName] )
                if not success:
                    log.error( "Can not divide: " + highName + " by " + nomSysName )
                    raise RuntimeError("Divide by zero.")
                else:
                    log.info(highName + " * " + nomName)
                    configMgr.hists[highName].Multiply( configMgr.hists[nomName] )
                    pass

        if self.noRenormSys and normalizeSys:
            log.debug("    sample.noRenormSys==True and normalizeSys==True for sample <%s> and syst <%s>. Setting normalizeSys to False."%(self.name, systName))
            normalizeSys = False

        if normalizeSys and not self.normRegions: 
            log.error("    normalizeSys==True but no normalization regions specified. This should never happen!")
            normChannels = []
            tl = self.parentChannel.parentTopLvl
            for ch in tl.channels:
                if (ch.channelName in tl.bkgConstrainChannels) or (ch.channelName in tl.signalChannels):
                    normChannels.append( (ch.regionString, ch.variableName) )
                    pass
                pass
            self.setNormRegions(normChannels)
            log.warning("            For now, using all non-validation channels by default: %s"%self.normRegions)

        ## Three use-cases:
        ## 1. Normalized systematics over control regions, and all sub-cases (symmetrize, includeOverallSys; symmetrizeEnvelope)
        ## 2. includeOverallSys and not normalizeSys:
        ## 3. No renormalization, and no overall-systematics

        if normalizeSys:
            log.verbose("Case 1: normalized systematic")

            if not self.normRegions: 
                raise RuntimeError("Please specify normalization regions!")
            
            if symmetrize and symmetrizeEnvelope:
                # build the envelope of up/down
                log.verbose("Symmetrizing envelope of histogram: building error = max ( (up-nom), (nom-down) )")
                log.verbose("(nom={0} / low={1} / high={2}".format(nomName, lowName, highName))
                symmetrizeSystematicEnvelope(nomName, lowName, highName)
            elif oneSide and symmetrize:
                # symmetrize
                configMgr.hists[lowName] = configMgr.hists[nomName].Clone(lowName)
                configMgr.hists[lowName].Scale(2.0)
                configMgr.hists[lowName].Add(configMgr.hists[highName],  -1.0)

                for iBin in xrange(1, configMgr.hists[lowName].GetNbinsX()+1):
                    binVal = configMgr.hists[lowName].GetBinContent(iBin)
                    if binVal<0.:
                        configMgr.hists[lowName].SetBinContent(iBin, 0.)
            
            # use different renormalization region
            if len(self.normSampleRemap) > 0: 
                samNameRemap = self.normSampleRemap
                log.info("remapping normalization of <%s> to sample:  %s" % (samName,samNameRemap))
            else:
                samNameRemap = samName
                log.debug("Using samNameRemap = {0}".format(samName))

            highRemapName = "h"+samNameRemap+systName+"High_"+normString+"Norm"
            lowRemapName = "h"+samNameRemap+systName+"Low_"+normString+"Norm"
            nomRemapName = "h"+samNameRemap+"Nom_"+normString+"Norm"

            highIntegral = configMgr.hists[highRemapName].Integral()
            lowIntegral  = configMgr.hists[lowRemapName].Integral()
            nomIntegral  = configMgr.hists[nomRemapName].Integral()

            log.verbose("Loading high remap integral from {0}: {1}".format(highRemapName, highIntegral))
            log.verbose("Loading low remap integral from {0}: {1}".format(lowRemapName, lowIntegral))
            log.verbose("Loading nominal remap integral from {0}: {1}".format(nomRemapName, nomIntegral))
            
            if len(nomSysName) > 0:  ## renormalization done based on consistent set of trees
                if configMgr.hists[nomSysName] != None:
                    nomIntegral = configMgr.hists["h"+samNameRemap+systName+"Nom_"+normString+"Norm"].Integral()
            
            # Attempt to symmetrize 
            if oneSide and symmetrize:
                log.debug("Attempting to symmetrize one-sided systematic")
                lowIntegral = 2.*nomIntegral - highIntegral # NOTE: this is an approximation!
                if lowIntegral < 0:
                    lowIntegral = configMgr.hists["h"+samNameRemap+systName+"Low_"+normString+"Norm"].Integral()
                    if lowIntegral == 0:
                        lowIntegral = nomIntegral
                    
                    # clearly a problem. Revert to unsymmetrize
                    log.warning("    generating HistoSys for %s syst=%s low=0. Revert to non-symmetrize." % (nomName, systName))
                    symmetrize = False

            # Construct high/low from integrals
            try:
                high = highIntegral / nomIntegral
                low = lowIntegral / nomIntegral
                log.verbose("Determined high and low ratios w.r.t. nominal: {} and {}".format(high, low))
            except ZeroDivisionError:
                log.error("    generating HistoSys for %s syst=%s: nom=%g high=%g low=%g. Systematic is removed from fit." % (nomName, systName, nomIntegral, highIntegral, lowIntegral))
                return

            log.debug("Constructing cloned normalized histograms")
            configMgr.hists["%sNorm" % highName] = configMgr.hists[highName].Clone("%sNorm" % highName)
            configMgr.hists["%sNorm" % lowName] = configMgr.hists[lowName].Clone("%sNorm" % lowName)
           

            # Attempt to scale the high and low histograms down to normalized histograms
            try:
                log.debug("Scaling normalized histograms by integrals of remapped histograms: high with {}, low with {}".format(1.0/high, 1.0/low))
                configMgr.hists[highName+"Norm"].Scale(1./high)
                configMgr.hists[lowName+"Norm"].Scale(1./low)
            except ZeroDivisionError:
                log.error("    generating HistoSys for %s syst=%s: nom=%g high=%g low=%g. Systematic is removed from fit." % (nomName, systName, nomIntegral, highIntegral, lowIntegral))
                return
            
            # Attempt to generate an overallNormHistoSys if required
            if includeOverallSys and not (oneSide and not symmetrize):
                log.debug("Attempting to build overallNormHistoSys")
                nomIntegralN = configMgr.hists[nomName].Integral()
                lowIntegralN = configMgr.hists[lowName+"Norm"].Integral()
                highIntegralN = configMgr.hists[highName+"Norm"].Integral()
            
                log.verbose("Loading high norm integral from {0}: {1}".format(highName+"Norm", highIntegralN))
                log.verbose("Loading low norm integral from {0}: {1}".format(lowName+"Norm", lowIntegralN))
                log.verbose("Loading nominal norm integral from {0}: {1}".format(nomName, nomIntegralN))

                if nomIntegralN == 0 or highIntegralN == 0 or lowIntegralN == 0:
                    # MB : cannot renormalize, so don't after all
                    log.warning("    will not generate overallNormHistoSys for %s syst=%s nom=%g high=%g low=%g. Revert to NormHistoSys." % (nomName, systName, nomIntegralN, highIntegralN, lowIntegralN))
                    includeOverallSys = False
                    pass
                else:
                    # renormalize
                    try:
                        highN = highIntegralN / nomIntegralN
                        lowN = lowIntegralN / nomIntegralN
                    except ZeroDivisionError:
                        log.error("    generating overallNormHistoSys for %s syst=%s nom=%g high=%g low=%g. Systematic is removed from fit." % (nomName, systName, nomIntegralN, highIntegralN, lowIntegralN))
                        return
                
                    try:
                        log.debug("Scaling normalized histograms: high with {}, low with {}".format(1.0/highN, 1.0/lowN))
                        configMgr.hists[highName+"Norm"].Scale(1./highN)
                        configMgr.hists[lowName+"Norm"].Scale(1./lowN)
                    except ZeroDivisionError:
                        log.error("    generating overallNormHistoSys for %s syst=%s nom=%g high=%g low=%g keeping in fit (offending histogram should be empty)." % (nomName, systName, nomIntegralN, highIntegralN, lowIntegralN))
                        return


            ## Check the shape and normalisation impact
            #
            # The chi2test can be performed on either the normal or the Norm histogram; since they're scaled
            # up and down by simple numbers, there is no effect. 
            # 
            # The normalisation check is just performed on highN and lowN. 

            #print configMgr.hists[highName+"Norm"].Integral()
            #print configMgr.hists[lowName+"Norm"].Integral()

            #print configMgr.hists[nomName].Chi2Test(configMgr.hists[highName+"Norm"], "WW UF OF P")
            #print configMgr.hists[nomName].Chi2Test(configMgr.hists[highName], "WW UF OF P")

            #print highN, lowN
            #print high, low
    
            # Now, finally add the systematic
            if oneSide and not symmetrize:
                ## MB : avoid swapping of histograms, always pass high and nominal
                self.histoSystList.append((systName, highName+"Norm", nomName, configMgr.histCacheFile, "", "", "", ""))
            else:
                self.histoSystList.append((systName, highName+"Norm", lowName+"Norm", configMgr.histCacheFile, "", "", "", ""))
           
            # Do we need to include an overall systematic?
            if includeOverallSys and not (oneSide and not symmetrize):
                self.addOverallSys(systName, highN, lowN)                
            

        # Case 2
        if includeOverallSys and not normalizeSys:
            log.verbose("Case 2: non-normalized systematic with includeOverallSys")
           
            # Symmetrization efforts: either an envelope, or the usual one
            if symmetrizeEnvelope:
                # build the envelope of up/down
                log.verbose("Symmetrizing envelope of histogram: building error = max ( (up-nom), (nom-down) )")
                symmetrizeSystematicEnvelope(nomName, lowName, highName)
            elif oneSide and symmetrize:
                # symmetrize
                configMgr.hists[lowName] = configMgr.hists[nomName].Clone(lowName)
                configMgr.hists[lowName].Scale(2.0)
                configMgr.hists[lowName].Add(configMgr.hists[highName],  -1.0)

                for iBin in xrange(1, configMgr.hists[lowName].GetNbinsX()+1):
                    binVal = configMgr.hists[lowName].GetBinContent(iBin)
                    if binVal < 0.:
                        configMgr.hists[lowName].SetBinContent(iBin, 0.)

            # Now construct high and low integrals for renormalization
            try:
                nomIntegral = configMgr.hists[nomName].Integral()
                lowIntegral = configMgr.hists[lowName].Integral()
                highIntegral = configMgr.hists[highName].Integral()
            except AttributeError:
                log.error("    generating HistoSys for %s syst=%s: one of the histograms is None. Systematic is removed from fit." % (nomName, systName))
                return

            # Check whether a renormalization actually makes sense
            if nomIntegral == 0 or lowIntegral == 0 or highIntegral == 0:
                # MB : cannot renormalize, so don't after all
                self.histoSystList.append((systName, highName, lowName, configMgr.histCacheFile, "", "", "", ""))

                ## TODO: check shape effect
                #if checkShapeEffect(configMgr.hists[nomName], configMgr.hists[highName], configMgr.hists[lowName] ):
                    #log.error("    generating HistoSys for %s syst=%s nom=%g high=%g low=%g: cannot renormalize; only using shape" % (nomName, systName, nomIntegral, highIntegral, lowIntegral))
                    #self.histoSystList.append((systName, highName, lowName, configMgr.histCacheFile, "", "", "", ""))
                #else:
                    #log.error("    generating HistoSys for %s syst=%s nom=%g high=%g low=%g: cannot renormalize, no shape effect. Systematic is removed from fit." % (nomName, systName, nomIntegral, highIntegral, lowIntegral))
                    #return
            else:
                # renormalize
                try:
                    high = highIntegral / nomIntegral
                    low = lowIntegral / nomIntegral
                except ZeroDivisionError:
                    log.error("    generating HistoSys for %s syst=%s: nom=%g high=%g low=%g. Systematic is removed from fit." % (nomName, systName, nomIntegral, highIntegral, lowIntegral))
                    return
                
                configMgr.hists[highName+"Norm"] = configMgr.hists[highName].Clone(highName+"Norm")
                configMgr.hists[lowName+"Norm"] = configMgr.hists[lowName].Clone(lowName+"Norm")
                
                try:
                    configMgr.hists[highName+"Norm"].Scale(1./high)
                    configMgr.hists[lowName+"Norm"].Scale(1./low)
                except ZeroDivisionError:
                    log.error("    generating HistoSys for %s syst=%s: nom=%g high=%g low=%g keeping in fit (offending histogram should be empty)." % (nomName, systName, nomIntegral, highIntegral, lowIntegral))
                    return
                    
                self.histoSystList.append((systName, highName+"Norm", lowName+"Norm", configMgr.histCacheFile, "", "", "", ""))
                self.addOverallSys(systName, high, low)

                ## And finally add the systematic
                ## TODO: check shape effect
                #if checkShapeEffect(configMgr.hists[nomName], configMgr.hists[highName], configMgr.hists[lowName] ):
                    #self.histoSystList.append((systName, highName+"Norm", lowName+"Norm", configMgr.histCacheFile, "", "", "", ""))
                #else:
                    #log.error("    generating HistoSys for %s syst=%s nom=%g high=%g low=%g has no impact on shape. Shape effect of systematic is removed from fit." % (nomName, systName, nomIntegral, highIntegral, lowIntegral))

                ## TODO: check norm effect
                #if max( abs(high-1.0), abs(1.0-low) ) < 0.005:
                    #log.error("    generating OverallSys for {} syst={} nom={:g} high={:g} low={:g}. Systematic has less than 0.5% impact and is removed from fit.".format(nomName, systName, nomIntegral, highIntegral, lowIntegral))
                #else: 
                    #self.addOverallSys(systName, high, low)

        # Case 3
        if not includeOverallSys and not normalizeSys: # no renormalization, and no overall systematic
            log.verbose("Case 3: non-normalized systematic without includeOverallSys")

            if symmetrize and not (oneSide or symmetrizeEnvelope): ## symmetrize the systematic uncertainty
                log.verbose("Symmetrizing histogram; _NOT_ using oneSide or symmetrizeEnvelope")
                nomIntegral = configMgr.hists[nomName].Integral()
                lowIntegral = configMgr.hists[lowName].Integral()
                highIntegral = configMgr.hists[highName].Integral()

                try:
                    high = highIntegral / nomIntegral
                    low = lowIntegral / nomIntegral
                except ZeroDivisionError:
                    log.error("    generating HistoSys for %s syst=%s nom=%g high=%g low=%g. Systematic is removed from fit." % (nomName, systName, nomIntegral, highIntegral, lowIntegral))
                    return

                if high < 1.0 and 1.0 > low > 0.0:
                    log.warning("    addHistoSys for %s: high=%f is < 1.0. Taking symmetric value from low %f => %f" % (systName, high, low, 2.-low))
                    configMgr.hists[highName+"Norm"] = configMgr.hists[highName].Clone(highName+"Norm")
                    try:
                        configMgr.hists[highName+"Norm"].Scale((2.0-low)/high)
                    except ZeroDivisionError:
                        log.error("    generating HistoSys for %s syst=%s nom=%g high=%g low=%g. Systematic is removed from fit." % (nomName, systName, nomIntegral, highIntegral, lowIntegral))
                        return
                    self.histoSystList.append((systName, highName+"Norm", lowName, configMgr.histCacheFile, "", "", "", ""))
                elif low > 1.0 and high > 1.0:
                    log.warning("    addHistoSys for %s: low=%f is > 1.0. Taking symmetric value from high %f => %f"% (systName, low, high, 2.-high))
                    configMgr.hists[lowName+"Norm"] = configMgr.hists[lowName].Clone(lowName+"Norm")
                    try:
                        configMgr.hists[lowName+"Norm"].Scale((2.0-high)/low)
                    except ZeroDivisionError:
                        log.error("    generating HistoSys for %s syst=%s nom=%g high=%g low=%g. Systematic is removed from fit." % (nomName, systName, nomIntegral, highIntegral, lowIntegral))
                        return
                    self.histoSystList.append((systName, highName, lowName+"Norm", configMgr.histCacheFile, "", "", "", ""))
                elif low < 0.0:
                    log.warning("    addHistoSys for %s: low=%f is < 0.0. Setting negative bins to 0.0." % (systName, low))
                    configMgr.hists[lowName+"Norm"] = configMgr.hists[lowName].Clone(lowName+"Norm")
                    for iBin in xrange(1, configMgr.hists[lowName+"Norm"].GetNbinsX()+1):
                        if configMgr.hists[lowName+"Norm"].GetBinContent(iBin) < 0.:
                            configMgr.hists[lowName+"Norm"].SetBinContent(iBin, 0.)
                    self.histoSystList.append((systName, highName, lowName+"Norm", configMgr.histCacheFile, "", "", "", ""))
                else:
                    self.histoSystList.append((systName, highName, lowName, configMgr.histCacheFile, "", "", "", ""))
            elif symmetrize and oneSide:
                log.verbose("Symmetrizing one-sided histogram: building low=(2*nominal)-high")
                # symmetrize one-side systematic, nothing else
                configMgr.hists[lowName] = configMgr.hists[nomName].Clone(lowName)
                configMgr.hists[lowName].Scale(2.0)
                configMgr.hists[lowName].Add(configMgr.hists[highName], -1.0)

                for iBin in xrange(1, configMgr.hists[lowName].GetNbinsX()+1):
                    binVal = configMgr.hists[lowName].GetBinContent(iBin)
                    if binVal < 0.:
                        configMgr.hists[lowName].SetBinContent(iBin, 0.)

                self.histoSystList.append((systName, highName, lowName, configMgr.histCacheFile, "", "", "", "")) 
            elif symmetrize and symmetrizeEnvelope:
                log.verbose("Symmetrizing envelope of histogram: building error = max ( (up-nom), (nom-down) )")
                symmetrizeSystematicEnvelope(nomName, lowName, highName)
                
                self.histoSystList.append((systName, highName, lowName, configMgr.histCacheFile, "", "", "", ""))
                
            else: # default: don't do anything special
                log.verbose("Adding a simple variation")

                nomIntegral = configMgr.hists[nomName].Integral()
                lowIntegral = configMgr.hists[lowName].Integral()
                highIntegral = configMgr.hists[highName].Integral()

                keepNorm = True
                if not checkNormalizationEffect(configMgr.hists[nomName], configMgr.hists[highName], configMgr.hists[lowName]):
                    log.error("    HistoSys for {} syst={} nom={:g} high={:g} low={:g} has small impact on normalisation.".format(nomName, systName, nomIntegral, highIntegral, lowIntegral))
                    keepNorm = False

                #if not checkShapeEffect(configMgr.hists[nomName], configMgr.hists[highName], configMgr.hists[lowName]):
                if not True: # TODO: checkShapeEffect() to be implemented 
                    if not keepNorm:
                        log.error("    HistoSys for {} syst={} nom={:g} high={:g} low={:g} has small impact on normalisation and no effect on shape. Removing from fit.".format(nomName, systName, nomIntegral, highIntegral, lowIntegral))
                        return
                        
                    log.error("    HistoSys for {} syst={} nom={:g} high={:g} low={:g} has small impact on shape. Using normalisation only.".format(nomName, systName, nomIntegral, highIntegral, lowIntegral))

                    for i in xrange(0, configMgr.hists[nomName].GetNbinsX()+2):
                        configMgr.hists[lowName].SetBinContent(i, configMgr.hists[nomName].GetBinContent(i))
                        configMgr.hists[highName].SetBinContent(i, configMgr.hists[nomName].GetBinContent(i))
                        
                    configMgr.hists[lowName].Scale(lowIntegral)
                    configMgr.hists[highName].Scale(highIntegral)

                self.histoSystList.append((systName, highName, lowName, configMgr.histCacheFile, "", "", "", ""))

        if not systName in configMgr.systDict.keys():
            self.systList.append(systName)
        return


    def addShapeSys(self, systName, nomName, highName, lowName, constraintType="Gaussian"):
        """
        Add a ShapeSys entry using the nominal,  high and low histograms

        @param systName Name of the systematic
        @param nomName Nominal name of the systematic
        @param highName Name of the systematic corresponding to +1sigma
        @param lowName Name of the systematic corresponding to -1sigma
        @param constraintType Type of the constraint in a string (default 'Gaussian')
        """

        highHistName = highName + "Norm"
        configMgr.hists[highHistName] = configMgr.hists[highName].Clone(highHistName)

        lowHistName = lowName + "Norm"
        configMgr.hists[lowHistName]  = configMgr.hists[lowName].Clone(lowHistName)

        nomHistName = nomName + "Norm"
        configMgr.hists[nomHistName]  = configMgr.hists[nomName].Clone(nomHistName)

        for iBin in xrange(configMgr.hists[highHistName].GetNbinsX()+1):
            try:
                configMgr.hists[highHistName].SetBinContent(iBin,  fabs((configMgr.hists[highHistName].GetBinContent(iBin) / configMgr.hists[nomName].GetBinContent(iBin)) - 1.0) )
                configMgr.hists[highHistName].SetBinError(iBin, 0.)
            except ZeroDivisionError:
                configMgr.hists[highHistName].SetBinContent(iBin, 0.)
                configMgr.hists[highHistName].SetBinError(iBin, 0.)

        for iBin in xrange(configMgr.hists[lowHistName].GetNbinsX()+1):
            try:
                configMgr.hists[lowHistName].SetBinContent(iBin,  fabs((configMgr.hists[lowHistName].GetBinContent(iBin) / configMgr.hists[nomName].GetBinContent(iBin)) - 1.0) )
                configMgr.hists[lowHistName].SetBinError(iBin, 0.)
            except ZeroDivisionError:
                configMgr.hists[lowHistName].SetBinContent(iBin, 0.)
                configMgr.hists[lowHistName].SetBinError(iBin, 0.)

        for iBin in xrange(configMgr.hists[nomHistName].GetNbinsX()+1):
            try:
                configMgr.hists[nomHistName].SetBinContent(iBin, max( configMgr.hists[highHistName].GetBinContent(iBin),
                                                                      configMgr.hists[lowHistName].GetBinContent(iBin)))
                log.debug("!!!!!! shapeSys %s bin %g value %g" % (systName, iBin, configMgr.hists[nomHistName].GetBinContent(iBin)))
                configMgr.hists[nomHistName].SetBinError(iBin, 0.)
            except ZeroDivisionError:
                configMgr.hists[nomHistName].SetBinContent(iBin, 0.)
                configMgr.hists[nomHistName].SetBinError(iBin, 0.)

        if not systName in configMgr.systDict.keys():
            self.systList.append(systName)

        return


    def addShapeStat(self, systName, nomName, constraintType="Gaussian", statErrorThreshold=None):
        """
        Add a ShapeStat entry using the nominal histogram

        @param systName Name of the systematic
        @param nomName Name of the nominal histogram for the systematic
        @param constraintType String indicating the type of costraint (default Gaussian)
        @param statErrorThreshold Optional threshold for size of the error; any bins for which the error is below this ratio are ignored
        """
        histName = nomName + "Norm"
        configMgr.hists[histName]  = configMgr.hists[nomName].Clone(histName)

        for iBin in xrange(configMgr.hists[histName].GetNbinsX()+1):
            try:
                ratio = configMgr.hists[nomName].GetBinError(iBin) / configMgr.hists[nomName].GetBinContent(iBin)
                if (statErrorThreshold is not None) and (ratio<statErrorThreshold): 
                    log.info( "shapeStat %s bin %g value %g, below threshold of: %g. Will ignore." % (systName, iBin, ratio, statErrorThreshold) )
                    ratio = 0.0   ## don't show if below threshold
                configMgr.hists[histName].SetBinContent( iBin, ratio )
                configMgr.hists[histName].SetBinError( iBin, 0. )
                log.debug("!!!!!! shapeStat %s bin %g value %g" % (systName, iBin, configMgr.hists[histName].GetBinContent(iBin)) )
            except ZeroDivisionError:
                configMgr.hists[histName].SetBinContent( iBin, 0. )
                configMgr.hists[histName].SetBinError( iBin, 0.)
        if not systName in configMgr.systDict.keys():
            self.systList.append(systName)
        return


    def addOverallSys(self, systName, high, low):
        """
        Add an OverallSys entry using the high and low values
        
        @param systName Name of the systematic
        @param high Value at +1sigma
        @param low Value at -1sigma
        """
        
        if high == 1.0 and low == 1.0:
            log.warning("    addOverallSys for %s: high == 1.0 and low == 1.0. Systematic is removed from fit" % systName)
            return

        if high == 0.0 and low == 0.0:
            log.warning("    addOverallSys for %s: high=%g low=%g. Systematic is removed from fit." % (systName, high, low))
            return

        if high == low:
            low = 2.0 - high
            log.error("    addOverallSys '%s' has invalid inputs: high == low == %.3f.\n    This would result in error=(high-low)/(high+low)=0, silently cancelled by HistFactory.\n    Please fix your user configuration.\n    For now, will recover by symmetrizing error: high=%.3f low=%.3f."%(systName,high,high,low))

        if high == 1.0 and low > 0.0 and low != 1.0:
            highOld = high
            high = 2.0 - low
            log.warning("    addOverallSys for %s: high=%g. Taking symmetric value from low %g => %g" % (systName, highOld, low, high))

        if low == 1.0 and high > 0.0 and high != 1.0:
            lowOld = low
            low = 2.0 - high
            log.warning("    addOverallSys for %s: low=%g. Taking symmetric value from high %g => %g" % (systName, lowOld, low, high))

        if low < 0.01:
            log.warning("    addOverallSys for %s: low=%g is < 0.01. Setting to low=0.01. High=%g." % (systName, low, high))
            low = 0.01

        if high < 0.01:
            log.warning("    addOverallSys for %s: high=%g is < 0.01. Setting to high=0.01. Low=%g." % (systName, high, low))
            high = 0.01
      
        #print high, high == 1.0
        #print low, low == 1.0

        # Perform these checks again after the symmetrisation
        if fabs(high-1.0) < 1E-5 and fabs(low-1.0) < 1E-5:
            log.warning("    addOverallSys for %s: high == 1.0 and low == 1.0. Systematic is removed from fit" % systName)
            return

        if fabs(high) < 1E-5 and fabs(low) < 1E-5:
            log.warning("    addOverallSys for %s: high=%g low=%g. Systematic is removed from fit." % (systName, high, low))
            return

        self.overallSystList.append((systName, high, low))
        if not systName in configMgr.systDict.keys():
            self.systList.append(systName)
        return

    def addNormFactor(self, name, val, high, low, const=False):
        """
        Add a normalization factor

        @param name Name of normalisation factor
        @param val Nominal value
        @param high Value at +1sigma
        @param low Value at -1sigma
        @param const Boolean that indicates whether the factor is constant or not
        """
        self.normFactor.append( (name, val, high, low, const) )
        if not name in configMgr.normList:
            configMgr.normList.append(name)
        return

    def setNormFactor(self, name, val, low, high, const=False):
        """
        Set normalization factor
        
        @param name Name of normalisation factor
        @param val Nominal value
        @param high Value at +1sigma
        @param low Value at -1sigma
        @param const Boolean that indicates whether the factor is constant or not
        """
        self.normFactor = []
        self.normFactor.append( (name, val, high, low, const) )
        if not name in configMgr.normList:
            configMgr.normList.append(name)
        return

    def addInput(self, filename, treename="", friends=[]):
        # add a file with a treename. If none given, fall back to sample name or our override 
        
        # NOTE: do NOT use self.treename for this -- it will include a suffix, 
        # leading to a doubly-suffixed tree in case a config-wide default jas beem set
        _treename = self.name
        if self.prefixTreeName != "":
            _treename = self.prefixTreeName

        if self.overrideTreename != "":
            _treename = self.overrideTreename

        if treename != "":
            _treename = treename

        #log.warning("calling addInput for {}".format(self.name))
        #log.warning("file = {}".format(filename))
        #log.warning("tree = {}".format(_treename))

        self.input_files.add(InputTree(filename, _treename, friends))

        # we are the leaves of the configMgr->fitConfig->channel->sample tree,
        # so no propagation necessary

    def addInputs(self, filenames, treename=""):
        # bulk add a bunch of filenames with the same treename
        for f in filenames:
            self.addInput(f, treename)
        
        # we are the leaves of the configMgr->fitConfig->channel->sample tree,
        # so no propagation necessary

    #def setFileList(self, filelist):
        #"""
        #Set file list for this Sample directly

        #@param filelist A list of filenames
        #"""
        #self.input_files = filelist

    #def setFile(self, file):
        #"""
        #Set file for this Sample directly

        #@param file a filename
        #"""
        #self.input_files = [file]

    #def propagateInputFiles(self, input_files):
        #"""
        #Propagate the file list downwards.
        
        #@param filelist A list of filenames
        #"""
        ## if we don't have our own file list,  use the one given to us
        #if not self.input_files:
            #self.input_files = input_files
        ## we are the leaves of the configMgr->fitConfig->channel->sample tree,
        ## so no propagation necessary

    def addShapeFactor(self, name):
        """
        Bin-by-bin factors to build histogram eg. for data-driven estimates

        @param name Name of the shape factor
        """
        self.shapeFactorList.append(name)

    def addSystematic(self, syst):
        """
        Add a systematic to this Sample directly. Will not overwrite existing systematics.

        @param syst An object of type Systematic
        """
        if self.isData:
            log.debug("Sample {} is data - not adding systematic {}".format(self.name, syst.name))
            return
        
        log.verbose("Adding systematic {} to sample {} ({})".format(syst.name, self.name, hex(id(self))))
        if syst.name in self.systDict.keys():
            raise Exception("Attempt to overwrite systematic %s in Sample %s (%s)" % (syst.name, self.name, hex(id(self))))
        else:
            self.systDict[syst.name] = syst.Clone()
            return

    def getOverallSys(self, name):
        """
        Get overall systematic by name

        @param name Name of the systematic to return
        """
        for syst in self.overallSystList:
            if name == syst[0]: return syst
        return None

    def replaceOverallSys(self, rsyst):
        """
        Replace overall systematic based on name. If no systematic of the name exists, nothing is replaced.

        @param rsyst Systematic object to replace the systematic with the same name
        """
        for idx in xrange(len(self.overallSystList)):
            syst = self.overallSystList[idx]
            if rsyst[0]==syst[0]:
                self.overallSystList[idx] = rsyst
                return

    def getHistoSys(self, name):
        """
        Return the systematic associated to the name

        @param name Name of the histoSys systematic
        """
        for syst in self.histoSystList:
            if name == syst[0]: return syst
        return None

    def replaceHistoSys(self, rsyst):
        """
        Replace histo systematic based on name. If no systematic of the name exists, nothing is replaced.

        @param rsyst Systematic object to replace the systematic with the same name
        """
        for idx in xrange(len(self.histoSystList)):
            syst = self.histoSystList[idx]
            if rsyst[0]==syst[0]:
                self.histoSystList[idx] = rsyst
                return

    def removeOverallSys(self, systName):
        """
        Remove overall systematic

        @param systName Name of the overall systematic to remove
        """
        for idx in xrange(len(self.overallSystList)):
            syst = self.overallSystList[idx]
            if systName==syst[0]:
                del self.overallSystList[idx]
                self.removeSystematic(systName)
                return

    def getAllSystematicNames(self):
        """
        Get all names of systematics associated to this sample
        """

        return self.systDict.keys()

    def getAllSystematics(self):
        """
        Return all systematics
        """

        return self.systDict

    def getSystematic(self, systName):
        """
        Get systematic from internal storage

        @param systName Name of the systematic to return
        """

        # protection against strange people who use getSystematic 
        # with the object they want to retrieve
        name = systName
        if isinstance(systName, SystematicBase):
            name = systName.name
        try:
            return self.systDict[name]
        except KeyError:
            log.warning("could not find systematic %s in sample %s" % (name, self.name))
        
        return

    def removeSystematic(self, systName):
        """
        Remove a systematic
        
        @param systName Name of the systematic to remove
        """
        # do we get a name or a Systematic passed?
        name = systName
        if isinstance(systName, SystematicBase):
            name = systName.name

        del self.systDict[name]

    def clearSystematics(self):
        """
        Remove all systematics from the sample
        """
        log.verbose("Clearing systematics for {} ({})".format(self.name, hex(id(self)))) 
        self.systDict.clear()
 
    def replaceSystematic(self, old, new):
        """
        Replace a systematic
        
        @param old Systematic object to remove
        @param new Systematic object to add
        """
        self.removeSystematic(old)
        self.addSystematic(new)
        pass
        
    # TODO: it would be nice to change the internal lists to dictionaries instead of arrays in a next iteration
    def createHistFactoryObject(self):
        """
        Construct the HistFactory object for this sample
        """
        s = ROOT.RooStats.HistFactory.Sample(self.name, self.histoName, configMgr.histCacheFile)
        s.SetNormalizeByTheory(self.normByTheory)
        if self.statConfig:
            s.ActivateStatError()
       
        #high = 1, low = 2
        for histoSys in self.histoSystList:
            s.AddHistoSys(histoSys[0], histoSys[2], configMgr.histCacheFile, "", 
                                       histoSys[1], configMgr.histCacheFile, "")

        for shapeSys in self.shapeSystList:
            constraintType = ROOT.RooStats.HistFactory.Constraint.GetType(shapeSys[2])
            s.AddShapeSys(shapeSys[0], constraintType, shapeSys[1], configMgr.histCacheFile)

        # high = 1, low = 2
        for overallSys in self.overallSystList:
            s.AddOverallSys(overallSys[0], overallSys[2], overallSys[1])

        for shapeFact in self.shapeFactorList:
            s.AddShapeFactor(shapeFact)

        # high = 2, low = 3
        if len(self.normFactor) > 0:
            for normFactor in self.normFactor:
                s.AddNormFactor(normFactor[0], normFactor[1], normFactor[3], normFactor[2], normFactor[4])

        return s

    def __str__(self):
        """
        Convert instance to XML string
        """
        self.sampleString = "  <Sample Name=\"%s\" HistoName=\"%s\" InputFile=\"%s\" NormalizeByTheory=\"%s\">\n"  % (self.name, self.histoName, configMgr.histCacheFile, self.normByTheory)
        
        if self.statConfig:
            self.sampleString += "    <StatError Activate=\"%s\"/>\n" % self.statConfig
        
        for histoSyst in self.histoSystList:
            self.sampleString += "    <HistoSys Name=\"%s\" HistoNameHigh=\"%s\" HistoNameLow=\"%s\" />\n" % (histoSyst[0], histoSyst[1], histoSyst[2])
        
        for shapeSyst in self.shapeSystList:
            self.sampleString += "    <ShapeSys Name=\"%s\" HistoName=\"%s\" ConstraintType=\"%s\"/>\n" % (shapeSyst[0], shapeSyst[1], shapeSyst[2])
        
        for overallSyst in self.overallSystList:
            self.sampleString += "    <OverallSys Name=\"%s\" High=\"%g\" Low=\"%g\" />\n" % (overallSyst[0], float(overallSyst[1]), float(overallSyst[2]))
        
        for shapeFact in self.shapeFactorList:
            self.sampleString += "    <ShapeFactor Name=\"%s\" />\n" % shapeFact
        
        if len(self.normFactor)>0:
            for normFactor in self.normFactor:
                self.sampleString += "    <NormFactor Name=\"%s\" Val=\"%g\" High=\"%g\" Low=\"%g\" Const=\"%s\" />\n" % (normFactor[0], normFactor[1], normFactor[2], normFactor[3], normFactor[4])
                pass
        
        self.sampleString += "  </Sample>\n\n"
        return self.sampleString
