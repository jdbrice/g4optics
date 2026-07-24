//
// ********************************************************************
// * License and Disclaimer                                           *
// *                                                                  *
// * The  Geant4 software  is  copyright of the Copyright Holders  of *
// * the Geant4 Collaboration.  It is provided  under  the terms  and *
// * conditions of the Geant4 Software License,  included in the file *
// * LICENSE and available at  http://cern.ch/geant4/license .  These *
// * include a list of copyright holders.                             *
// *                                                                  *
// * Neither the authors of this software system, nor their employing *
// * institutes,nor the agencies providing financial support for this *
// * work  make  any representation or  warranty, express or implied, *
// * regarding  this  software system or assume any liability for its *
// * use.  Please see the license in the file  LICENSE  and URL above *
// * for the full disclaimer and the limitation of liability.         *
// *                                                                  *
// * This  code  implementation is the result of  the  scientific and *
// * technical work of the GEANT4 collaboration.                      *
// * By using,  copying,  modifying or  distributing the software (or *
// * any work based  on the software)  you  agree  to acknowledge its *
// * use  in  resulting  scientific  publications,  and indicate your *
// * acceptance of all terms of the Geant4 Software license.          *
// ********************************************************************
//
/// \file optical/OpNovice2/include/RunAction.hh
/// \brief Definition of the RunAction class
//
//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......

#ifndef Run_h
#define Run_h 1

#include "G4OpBoundaryProcess.hh"
#include "G4Run.hh"
#include "G4ThreeVector.hh"

#include <array>
#include <limits>
#include <unordered_set>

class G4ParticleDefinition;

struct StackLayerEventRecord
{
  G4int generatedOptical = 0;
  G4int cerenkov = 0;
  G4int scintillation = 0;
  std::array<G4int, 2> sensorAllOrigin = {0, 0};
  std::array<G4int, 2> sensorLocalOrigin = {0, 0};
  G4double steelEnergyDeposit = 0.;
  G4int neutronElasticCount = 0;
  G4int neutronInelasticCount = 0;
  G4int neutronCaptureCount = 0;
  G4int chargedEntryCount = 0;
  G4double chargedEntryKineticEnergy = 0.;
  G4int electronEntryCount = 0;
  G4double electronEntryKineticEnergy = 0.;
  G4int protonEntryCount = 0;
  G4double protonEntryKineticEnergy = 0.;
  G4int otherChargedEntryCount = 0;
  G4double otherChargedEntryKineticEnergy = 0.;
  G4bool primaryNeutronTileEntryValid = false;
  G4ThreeVector primaryNeutronTileEntryPosition;
  G4double tileEnergyDeposit = 0.;
  G4double electronTileEnergyDeposit = 0.;
  G4double protonTileEnergyDeposit = 0.;
  G4double otherChargedTileEnergyDeposit = 0.;
  G4double neutralTileEnergyDeposit = 0.;
};

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
class Run : public G4Run
{
  public:
    static constexpr G4int kStackLayerCount = 10;
    static constexpr G4int kStackSensorsPerLayer = 2;
    static constexpr G4int kStackSensorCount =
      kStackLayerCount * kStackSensorsPerLayer;
    static constexpr G4int kUnknownOriginIndex = kStackLayerCount;

    Run();
    ~Run() override = default;

    void SetPrimary(G4ParticleDefinition* particle, G4double energy, G4bool polarized,
                    G4double polarization, const G4String& electronEnergyMode);

    //  particle energy
    void AddCerenkovEnergy(G4double en) { fCerenkovEnergy += en; }
    void AddScintillationEnergy(G4double en) { fScintEnergy += en; }
    void AddWLSAbsorptionEnergy(G4double en) { fWLSAbsorptionEnergy += en; }
    void AddWLSEmissionEnergy(G4double en) { fWLSEmissionEnergy += en; }
    void AddWLS2AbsorptionEnergy(G4double en) { fWLS2AbsorptionEnergy += en; }
    void AddWLS2EmissionEnergy(G4double en) { fWLS2EmissionEnergy += en; }

    // number of particles
    void AddCerenkov(G4int layer = -1)
    {
      fCerenkovCount += 1;
      fEventCerenkovCount += 1;
      fEventGeneratedOpticalCount += 1;
      if (layer >= 0 && layer < kStackLayerCount) {
        auto& record = fEventStackLayers[static_cast<std::size_t>(layer)];
        record.cerenkov += 1;
        record.generatedOptical += 1;
      }
    }
    void AddScintillation(const G4ThreeVector& creationPosition,
                          G4int layer = -1)
    {
      fScintCount += 1;
      fEventScintCount += 1;
      fEventGeneratedOpticalCount += 1;
      fEventScintPositionSum += creationPosition;
      if (layer >= 0 && layer < kStackLayerCount) {
        auto& record = fEventStackLayers[static_cast<std::size_t>(layer)];
        record.scintillation += 1;
        record.generatedOptical += 1;
      }
    }
    void AddRayleigh() { fRayleighCount += 1; }
    void AddWLSAbsorption() { fWLSAbsorptionCount += 1; }
    void AddWLSEmission()
    {
      fWLSEmissionCount += 1;
      fEventGeneratedOpticalCount += 1;
    }
    void AddWLS2Absorption() { fWLS2AbsorptionCount += 1; }
    void AddWLS2Emission()
    {
      fWLS2EmissionCount += 1;
      fEventGeneratedOpticalCount += 1;
    }

    void AddOpAbsorption() { fOpAbsorption += 1; }
    void AddOpAbsorptionPrior() { fOpAbsorptionPrior += 1; }

    void AddFresnelRefraction() { fBoundaryProcs[FresnelRefraction] += 1; }
    void AddFresnelReflection() { fBoundaryProcs[FresnelReflection] += 1; }
    void AddTransmission() { fBoundaryProcs[Transmission] += 1; }
    void AddTotalInternalReflection() { fBoundaryProcs[TotalInternalReflection] += 1; }
    void AddLambertianReflection() { fBoundaryProcs[LambertianReflection] += 1; }
    void AddLobeReflection() { fBoundaryProcs[LobeReflection] += 1; }
    void AddSpikeReflection() { fBoundaryProcs[SpikeReflection] += 1; }
    void AddBackScattering() { fBoundaryProcs[BackScattering] += 1; }
    void AddAbsorption() { fBoundaryProcs[Absorption] += 1; }
    void AddDetection() { fBoundaryProcs[Detection] += 1; }
    void AddNotAtBoundary() { fBoundaryProcs[NotAtBoundary] += 1; }
    void AddSameMaterial() { fBoundaryProcs[SameMaterial] += 1; }
    void AddStepTooSmall() { fBoundaryProcs[StepTooSmall] += 1; }
    void AddNoRINDEX() { fBoundaryProcs[NoRINDEX] += 1; }

    void AddTotalSurface() { fTotalSurface += 1; }
    void AddPolishedLumirrorAirReflection() { fBoundaryProcs[PolishedLumirrorAirReflection] += 1; }
    void AddPolishedLumirrorGlueReflection()
    {
      fBoundaryProcs[PolishedLumirrorGlueReflection] += 1;
    }
    void AddPolishedAirReflection() { fBoundaryProcs[PolishedAirReflection] += 1; }
    void AddPolishedTeflonAirReflection() { fBoundaryProcs[PolishedTeflonAirReflection] += 1; }
    void AddPolishedTiOAirReflection() { fBoundaryProcs[PolishedTiOAirReflection] += 1; }
    void AddPolishedTyvekAirReflection() { fBoundaryProcs[PolishedTyvekAirReflection] += 1; }
    void AddPolishedVM2000AirReflection() { fBoundaryProcs[PolishedVM2000AirReflection] += 1; }
    void AddPolishedVM2000GlueReflection() { fBoundaryProcs[PolishedVM2000GlueReflection] += 1; }

    void AddEtchedLumirrorAirReflection() { fBoundaryProcs[EtchedLumirrorAirReflection] += 1; }
    void AddEtchedLumirrorGlueReflection() { fBoundaryProcs[EtchedLumirrorGlueReflection] += 1; }
    void AddEtchedAirReflection() { fBoundaryProcs[EtchedAirReflection] += 1; }
    void AddEtchedTeflonAirReflection() { fBoundaryProcs[EtchedTeflonAirReflection] += 1; }
    void AddEtchedTiOAirReflection() { fBoundaryProcs[EtchedTiOAirReflection] += 1; }
    void AddEtchedTyvekAirReflection() { fBoundaryProcs[EtchedTyvekAirReflection] += 1; }
    void AddEtchedVM2000AirReflection() { fBoundaryProcs[EtchedVM2000AirReflection] += 1; }
    void AddEtchedVM2000GlueReflection() { fBoundaryProcs[EtchedVM2000GlueReflection] += 1; }

    void AddGroundLumirrorAirReflection() { fBoundaryProcs[GroundLumirrorAirReflection] += 1; }
    void AddGroundLumirrorGlueReflection() { fBoundaryProcs[GroundLumirrorGlueReflection] += 1; }
    void AddGroundAirReflection() { fBoundaryProcs[GroundAirReflection] += 1; }
    void AddGroundTeflonAirReflection() { fBoundaryProcs[GroundTeflonAirReflection] += 1; }
    void AddGroundTiOAirReflection() { fBoundaryProcs[GroundTiOAirReflection] += 1; }
    void AddGroundTyvekAirReflection() { fBoundaryProcs[GroundTyvekAirReflection] += 1; }
    void AddGroundVM2000AirReflection() { fBoundaryProcs[GroundVM2000AirReflection] += 1; }
    void AddGroundVM2000GlueReflection() { fBoundaryProcs[GroundVM2000GlueReflection] += 1; }

    void AddDichroic() { fBoundaryProcs[Dichroic] += 1; }
    void AddCoatedDielectricRefraction() { fBoundaryProcs[CoatedDielectricRefraction] += 1; }
    void AddCoatedDielectricReflection() { fBoundaryProcs[CoatedDielectricReflection] += 1; }
    void AddCoatedDielectricFrustratedTransmission()
    {
      fBoundaryProcs[CoatedDielectricFrustratedTransmission] += 1;
    }

    void Merge(const G4Run*) override;

    void EndOfRun();

    // Per-event bookkeeping for scan ntuples.
    void BeginEvent(G4int eventID = -1);
    void CommitEventStatistics();
    void AddPrimaryKineticEnergy(G4double energy);
    void AddDecayBetaEnergy(G4double energy);
    void AddShootPosition(const G4ThreeVector& pos);
    void SetPrimaryHitPosition(const G4ThreeVector& pos);
    void SetPrimaryNeutronTileEntry(const G4ThreeVector& pos, G4int layer = -1);
    void AddScintillationCentroid(const G4ThreeVector& pos);
    void AddSteelEnergyDeposit(G4double energy, G4int layer = -1);
    void AddPrimaryNeutronElasticInteraction(G4int layer = -1);
    void AddPrimaryNeutronInelasticInteraction(G4int layer = -1);
    void AddPrimaryNeutronCaptureInteraction(G4int layer = -1);
    G4bool RecordChargedTileEntry(G4int trackID,
                                  G4int pdgEncoding,
                                  G4double charge,
                                  G4double kineticEnergy,
                                  G4int layer = -1);
    void AddTileEnergyDeposit(G4int pdgEncoding,
                              G4double charge,
                              G4double energy,
                              G4int layer = -1);

    const StackLayerEventRecord& GetEventStackLayer(G4int layer) const;
    G4int GetEventStackTransfer(G4int originLayer, G4int sensorCopy) const;
    G4int GetEventUnknownOriginDetections() const;

    G4long GetGeneratedOpticalCount() const
    {
      return fCerenkovCount + fScintCount + fWLSEmissionCount + fWLS2EmissionCount;
    }
    G4long GetCerenkovCount() const { return fCerenkovCount; }
    G4long GetScintillationCount() const { return fScintCount; }
    G4long GetSiPMDetectionCount() const { return fSiPMDetectionCount; }
    G4int GetNumberOfEvents() const { return numberOfEvent; }
    G4int GetEventGeneratedOpticalCount() const { return fEventGeneratedOpticalCount; }
    G4int GetEventCerenkovCount() const { return fEventCerenkovCount; }
    G4int GetEventScintillationCount() const { return fEventScintCount; }
    G4int GetEventSiPMDetectionCount() const { return fEventSiPMDetectionCount; }
    G4int GetEventSiPMDetectionCount(G4int sensorIndex) const
    {
      return sensorIndex >= 0 && sensorIndex < 4
               ? fEventSiPMDetectionCounts[static_cast<std::size_t>(sensorIndex)]
               : 0;
    }
    G4bool HasEventHitPosition() const { return fEventHitValid; }
    G4ThreeVector GetEventHitPosition() const { return fEventHitPosition; }
    G4bool HasEventScintillationCentroid() const { return fEventScintCount > 0; }
    G4ThreeVector GetEventScintillationCentroid() const;

    G4double GetEventSteelEnergyDeposit() const { return fEventSteelEnergyDeposit; }
    G4int GetEventPrimaryNeutronElasticCount() const
    {
      return fEventPrimaryNeutronElasticCount;
    }
    G4int GetEventPrimaryNeutronInelasticCount() const
    {
      return fEventPrimaryNeutronInelasticCount;
    }
    G4int GetEventPrimaryNeutronCaptureCount() const
    {
      return fEventPrimaryNeutronCaptureCount;
    }
    G4int GetEventChargedTileEntryCount() const { return fEventChargedTileEntryCount; }
    G4double GetEventChargedTileEntryKineticEnergy() const
    {
      return fEventChargedTileEntryKineticEnergy;
    }
    G4int GetEventElectronTileEntryCount() const { return fEventElectronTileEntryCount; }
    G4double GetEventElectronTileEntryKineticEnergy() const
    {
      return fEventElectronTileEntryKineticEnergy;
    }
    G4int GetEventProtonTileEntryCount() const { return fEventProtonTileEntryCount; }
    G4double GetEventProtonTileEntryKineticEnergy() const
    {
      return fEventProtonTileEntryKineticEnergy;
    }
    G4int GetEventOtherChargedTileEntryCount() const
    {
      return fEventOtherChargedTileEntryCount;
    }
    G4double GetEventOtherChargedTileEntryKineticEnergy() const
    {
      return fEventOtherChargedTileEntryKineticEnergy;
    }
    G4bool HasEventPrimaryNeutronTileEntry() const
    {
      return fEventPrimaryNeutronTileEntryValid;
    }
    G4ThreeVector GetEventPrimaryNeutronTileEntryPosition() const
    {
      return fEventPrimaryNeutronTileEntryPosition;
    }
    G4double GetEventTileEnergyDeposit() const { return fEventTileEnergyDeposit; }
    G4double GetEventElectronTileEnergyDeposit() const
    {
      return fEventElectronTileEnergyDeposit;
    }
    G4double GetEventProtonTileEnergyDeposit() const
    {
      return fEventProtonTileEnergyDeposit;
    }
    G4double GetEventOtherChargedTileEnergyDeposit() const
    {
      return fEventOtherChargedTileEnergyDeposit;
    }
    G4double GetEventNeutralTileEnergyDeposit() const
    {
      return fEventNeutralTileEnergyDeposit;
    }

    G4long GetCommittedEventCount() const { return fCommittedEventCount; }
    G4double GetSteelEnergyDepositSum() const;
    G4double GetSteelEnergyDepositMean() const;
    G4double GetSteelEnergyDepositRms() const;
    G4double GetSteelEnergyDepositStandardError() const;
    G4long GetSteelEnergyDepositNonzeroEventCount() const;
    G4long GetPrimaryNeutronElasticInteractionCount() const;
    G4long GetPrimaryNeutronInelasticInteractionCount() const;
    G4long GetPrimaryNeutronCaptureInteractionCount() const;
    G4long GetPrimaryNeutronElasticEventCount() const;
    G4long GetPrimaryNeutronInelasticEventCount() const;
    G4long GetPrimaryNeutronCaptureEventCount() const;
    G4long GetPrimaryNeutronInteractionEventCount() const;
    G4long GetChargedTileEntryCount() const;
    G4long GetChargedTileEntryEventCount() const;
    G4double GetChargedTileEntryKineticEnergySum() const;
    G4long GetElectronTileEntryCount() const;
    G4double GetElectronTileEntryKineticEnergySum() const;
    G4long GetProtonTileEntryCount() const;
    G4double GetProtonTileEntryKineticEnergySum() const;
    G4long GetOtherChargedTileEntryCount() const;
    G4double GetOtherChargedTileEntryKineticEnergySum() const;
    G4long GetPrimaryNeutronTileEntryEventCount() const;
    G4double GetTileEnergyDepositSum() const;
    G4double GetElectronTileEnergyDepositSum() const;
    G4double GetProtonTileEnergyDepositSum() const;
    G4double GetOtherChargedTileEnergyDepositSum() const;
    G4double GetNeutralTileEnergyDepositSum() const;
    G4double GetTileEnergyDepositMean() const;
    G4double GetTileEnergyDepositRms() const;
    G4double GetTileEnergyDepositStandardError() const;
    G4long GetTileEnergyDepositNonzeroEventCount() const;
    G4double GetGeneratedOpticalMean() const;
    G4double GetGeneratedOpticalRms() const;
    G4double GetGeneratedOpticalStandardError() const;
    G4long GetGeneratedOpticalNonzeroEventCount() const;
    G4double GetScintillationMean() const;
    G4double GetScintillationRms() const;
    G4double GetScintillationStandardError() const;
    G4long GetScintillationNonzeroEventCount() const;
    G4double GetSiPMDetectionMean() const;
    G4double GetSiPMDetectionRms() const;
    G4double GetSiPMDetectionStandardError() const;
    G4long GetSiPMDetectionNonzeroEventCount() const;

    G4int GetShootPositionCount() const { return fShootPositionCount; }
    G4ThreeVector GetMeanShootPosition() const;
    G4int GetHitPositionCount() const { return fHitPositionCount; }
    G4ThreeVector GetMeanHitPosition() const;
    G4int GetScintillationCentroidCount() const { return fScintCentroidCount; }
    G4ThreeVector GetMeanScintillationCentroid() const;
    G4int GetPrimaryKineticEnergyCount() const { return fPrimaryEnergyCount; }
    G4double GetPrimaryKineticEnergyMean() const;
    G4double GetPrimaryKineticEnergyRms() const;
    G4double GetPrimaryKineticEnergyMin() const { return fPrimaryEnergyMin; }
    G4double GetPrimaryKineticEnergyMax() const { return fPrimaryEnergyMax; }
    G4int GetCurrentEventID() const { return fCurrentEventID; }
    G4int GetDecayBetaCount() const { return fDecayBetaCount; }
    G4double GetDecayBetaEnergyMean() const;
    G4double GetDecayBetaEnergyRms() const;
    G4double GetDecayBetaEnergyMin() const { return fDecayBetaEnergyMin; }
    G4double GetDecayBetaEnergyMax() const { return fDecayBetaEnergyMax; }

    // SiPM Detection
    void AddSiPMDetection(G4int sensorIndex = 0, G4int originLayer = -1);

  private:
    // primary particle
    G4ParticleDefinition* fParticle = nullptr;
    G4double fEkin = -1.;
    G4bool fPolarized = false;
    G4double fPolarization = 0.;
    G4String fElectronEnergyMode = "fixed";

    G4double fCerenkovEnergy = 0.;
    G4double fScintEnergy = 0.;
    G4double fWLSAbsorptionEnergy = 0.;
    G4double fWLSEmissionEnergy = 0.;
    G4double fWLS2AbsorptionEnergy = 0.;
    G4double fWLS2EmissionEnergy = 0.;

    // number of particles
    G4long fCerenkovCount = 0;
    G4long fScintCount = 0;
    G4long fWLSAbsorptionCount = 0;
    G4long fWLSEmissionCount = 0;
    G4long fWLS2AbsorptionCount = 0;
    G4long fWLS2EmissionCount = 0;
    // number of events
    G4long fRayleighCount = 0;

    // non-boundary processes
    G4long fOpAbsorption = 0;

    // prior to boundary:
    G4long fOpAbsorptionPrior = 0;

    // boundary proc
    std::vector<G4long> fBoundaryProcs;

    G4long fTotalSurface = 0;

    // SiPM counting
    G4long fSiPMDetectionCount = 0;
    std::array<G4long, 4> fSiPMDetectionCounts = {0, 0, 0, 0};

    // Current event counts used for the Week 5.3 scan ntuple.
    G4int fCurrentEventID = -1;
    G4int fEventGeneratedOpticalCount = 0;
    G4int fEventCerenkovCount = 0;
    G4int fEventScintCount = 0;
    G4int fEventSiPMDetectionCount = 0;
    std::array<G4int, 4> fEventSiPMDetectionCounts = {0, 0, 0, 0};
    G4bool fEventHitValid = false;
    G4ThreeVector fEventHitPosition;
    G4ThreeVector fEventScintPositionSum;

    // Causal-chain observables for the realistic-neutron study.
    G4double fEventSteelEnergyDeposit = 0.;
    G4int fEventPrimaryNeutronElasticCount = 0;
    G4int fEventPrimaryNeutronInelasticCount = 0;
    G4int fEventPrimaryNeutronCaptureCount = 0;
    std::unordered_set<G4long> fEventChargedTileEntryTrackIDs;
    std::array<std::unordered_set<G4int>, kStackLayerCount>
      fEventStackChargedTileEntryTrackIDs;
    G4int fEventChargedTileEntryCount = 0;
    G4double fEventChargedTileEntryKineticEnergy = 0.;
    G4int fEventElectronTileEntryCount = 0;
    G4double fEventElectronTileEntryKineticEnergy = 0.;
    G4int fEventProtonTileEntryCount = 0;
    G4double fEventProtonTileEntryKineticEnergy = 0.;
    G4int fEventOtherChargedTileEntryCount = 0;
    G4double fEventOtherChargedTileEntryKineticEnergy = 0.;
    G4bool fEventPrimaryNeutronTileEntryValid = false;
    G4ThreeVector fEventPrimaryNeutronTileEntryPosition;
    G4double fEventTileEnergyDeposit = 0.;
    G4double fEventElectronTileEnergyDeposit = 0.;
    G4double fEventProtonTileEnergyDeposit = 0.;
    G4double fEventOtherChargedTileEnergyDeposit = 0.;
    G4double fEventNeutralTileEnergyDeposit = 0.;
    std::array<StackLayerEventRecord, kStackLayerCount> fEventStackLayers;
    std::array<std::array<G4int, kStackSensorCount>, kStackLayerCount + 1>
      fEventStackTransfers = {};
    G4bool fEventStatisticsCommitted = false;

    struct EventMoments
    {
      G4double sum = 0.;
      G4double sum2 = 0.;
      G4long nonzeroCount = 0;

      void Add(G4double value)
      {
        sum += value;
        sum2 += value * value;
        if (value != 0.) {
          nonzeroCount += 1;
        }
      }

      void Merge(const EventMoments& other)
      {
        sum += other.sum;
        sum2 += other.sum2;
        nonzeroCount += other.nonzeroCount;
      }
    };

    G4long fCommittedEventCount = 0;
    EventMoments fSteelEnergyDepositMoments;
    EventMoments fPrimaryNeutronElasticMoments;
    EventMoments fPrimaryNeutronInelasticMoments;
    EventMoments fPrimaryNeutronCaptureMoments;
    EventMoments fPrimaryNeutronInteractionMoments;
    EventMoments fChargedTileEntryCountMoments;
    EventMoments fChargedTileEntryKineticEnergyMoments;
    EventMoments fElectronTileEntryCountMoments;
    EventMoments fElectronTileEntryKineticEnergyMoments;
    EventMoments fProtonTileEntryCountMoments;
    EventMoments fProtonTileEntryKineticEnergyMoments;
    EventMoments fOtherChargedTileEntryCountMoments;
    EventMoments fOtherChargedTileEntryKineticEnergyMoments;
    EventMoments fPrimaryNeutronTileEntryMoments;
    EventMoments fTileEnergyDepositMoments;
    EventMoments fElectronTileEnergyDepositMoments;
    EventMoments fProtonTileEnergyDepositMoments;
    EventMoments fOtherChargedTileEnergyDepositMoments;
    EventMoments fNeutralTileEnergyDepositMoments;
    EventMoments fGeneratedOpticalMoments;
    EventMoments fScintillationMoments;
    EventMoments fSiPMDetectionMoments;

    G4double GetEventMean(const EventMoments& moments) const;
    G4double GetEventRms(const EventMoments& moments) const;
    G4double GetEventStandardError(const EventMoments& moments) const;

    // Per-run position means for scan-point summary CSVs.
    G4int fShootPositionCount = 0;
    G4ThreeVector fShootPositionSum;
    G4int fHitPositionCount = 0;
    G4ThreeVector fHitPositionSum;
    G4int fScintCentroidCount = 0;
    G4ThreeVector fScintCentroidSum;

    // Per-run primary kinetic-energy statistics for Sr-90 spectrum QA.
    G4int fPrimaryEnergyCount = 0;
    G4double fPrimaryEnergySum = 0.;
    G4double fPrimaryEnergySum2 = 0.;
    G4double fPrimaryEnergyMin = std::numeric_limits<G4double>::infinity();
    G4double fPrimaryEnergyMax = -std::numeric_limits<G4double>::infinity();

    // Per-run beta-electron statistics from radioactive decay tracks.
    G4int fDecayBetaCount = 0;
    G4double fDecayBetaEnergySum = 0.;
    G4double fDecayBetaEnergySum2 = 0.;
    G4double fDecayBetaEnergyMin = std::numeric_limits<G4double>::infinity();
    G4double fDecayBetaEnergyMax = -std::numeric_limits<G4double>::infinity();
};

#endif /* Run_h */
