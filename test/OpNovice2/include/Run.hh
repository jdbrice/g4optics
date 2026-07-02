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

#include <limits>

class G4ParticleDefinition;

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
class Run : public G4Run
{
  public:
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
    void AddCerenkov()
    {
      fCerenkovCount += 1;
      fEventGeneratedOpticalCount += 1;
    }
    void AddScintillation(const G4ThreeVector& creationPosition)
    {
      fScintCount += 1;
      fEventScintCount += 1;
      fEventGeneratedOpticalCount += 1;
      fEventScintPositionSum += creationPosition;
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
    void AddPrimaryKineticEnergy(G4double energy);
    void AddDecayBetaEnergy(G4double energy);
    void AddShootPosition(const G4ThreeVector& pos);
    void SetPrimaryHitPosition(const G4ThreeVector& pos);
    void AddScintillationCentroid(const G4ThreeVector& pos);
    G4int GetGeneratedOpticalCount() const
    {
      return fCerenkovCount + fScintCount + fWLSEmissionCount + fWLS2EmissionCount;
    }
    G4int GetScintillationCount() const { return fScintCount; }
    G4int GetSiPMDetectionCount() const { return fSiPMDetectionCount; }
    G4int GetNumberOfEvents() const { return numberOfEvent; }
    G4int GetEventGeneratedOpticalCount() const { return fEventGeneratedOpticalCount; }
    G4int GetEventScintillationCount() const { return fEventScintCount; }
    G4int GetEventSiPMDetectionCount() const { return fEventSiPMDetectionCount; }
    G4bool HasEventHitPosition() const { return fEventHitValid; }
    G4ThreeVector GetEventHitPosition() const { return fEventHitPosition; }
    G4bool HasEventScintillationCentroid() const { return fEventScintCount > 0; }
    G4ThreeVector GetEventScintillationCentroid() const;

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
    void AddSiPMDetection()
    {
      fSiPMDetectionCount += 1;
      fEventSiPMDetectionCount += 1;
    }

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
    G4int fCerenkovCount = 0;
    G4int fScintCount = 0;
    G4int fWLSAbsorptionCount = 0;
    G4int fWLSEmissionCount = 0;
    G4int fWLS2AbsorptionCount = 0;
    G4int fWLS2EmissionCount = 0;
    // number of events
    G4int fRayleighCount = 0;

    // non-boundary processes
    G4int fOpAbsorption = 0;

    // prior to boundary:
    G4int fOpAbsorptionPrior = 0;

    // boundary proc
    std::vector<G4int> fBoundaryProcs;

    G4int fTotalSurface = 0;

    // SiPM counting
    G4int fSiPMDetectionCount = 0;

    // Current event counts used for the Week 5.3 scan ntuple.
    G4int fCurrentEventID = -1;
    G4int fEventGeneratedOpticalCount = 0;
    G4int fEventScintCount = 0;
    G4int fEventSiPMDetectionCount = 0;
    G4bool fEventHitValid = false;
    G4ThreeVector fEventHitPosition;
    G4ThreeVector fEventScintPositionSum;

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
