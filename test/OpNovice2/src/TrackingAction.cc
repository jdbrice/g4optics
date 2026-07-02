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
/// \file optical/OpNovice2/src/TrackingAction.cc
/// \brief Implementation of the TrackingAction class
//
//

#include "TrackingAction.hh"

#include "Run.hh"
#include "TrackInformation.hh"

#include "G4AnalysisManager.hh"
#include "G4ParticleDefinition.hh"
#include "G4RunManager.hh"
#include "G4SystemOfUnits.hh"
#include "G4Track.hh"
#include "G4TrackingManager.hh"
#include "G4VProcess.hh"

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
void TrackingAction::PreUserTrackingAction(const G4Track* aTrack)
{
  // Create trajectory only for track in tracking region
  auto trackInfo = (TrackInformation*)(aTrack->GetUserInformation());

  if (!trackInfo) {
    trackInfo = new TrackInformation(aTrack);
    trackInfo->SetIsFirstTankX(true);
    aTrack->SetUserInformation(trackInfo);
  }

  trackInfo->SetIsFirstTankX(true);

  const auto creator = aTrack->GetCreatorProcess();
  const G4String creatorName = creator ? creator->GetProcessName() : "";
  const G4bool isDecayBeta =
    trackInfo->GetIsDecayBeta()
    &&
    aTrack->GetDefinition()->GetParticleName() == "e-"
    && creatorName.find("RadioactiveDecay") != G4String::npos;
  if (!isDecayBeta) {
    return;
  }

  const G4double kineticEnergy = aTrack->GetKineticEnergy();
  auto run = static_cast<Run*>(G4RunManager::GetRunManager()->GetNonConstCurrentRun());
  if (run) {
    run->AddDecayBetaEnergy(kineticEnergy);
  }

  G4String parentName = trackInfo->GetDecayBetaParentName();
  if (parentName.empty()) {
    parentName = "unknown";
  }
  G4String creatorProcess = trackInfo->GetDecayBetaCreatorProcess();
  if (creatorProcess.empty()) {
    creatorProcess = creatorName;
  }

  const auto position = aTrack->GetVertexPosition();
  auto analysisMan = G4AnalysisManager::Instance();
  analysisMan->FillNtupleIColumn(1, 0, run ? run->GetCurrentEventID() : -1);
  analysisMan->FillNtupleIColumn(1, 1, aTrack->GetTrackID());
  analysisMan->FillNtupleSColumn(1, 2, parentName);
  analysisMan->FillNtupleSColumn(1, 3, creatorProcess);
  analysisMan->FillNtupleDColumn(1, 4, kineticEnergy / MeV);
  analysisMan->FillNtupleDColumn(1, 5, position.x() / mm);
  analysisMan->FillNtupleDColumn(1, 6, position.y() / mm);
  analysisMan->FillNtupleDColumn(1, 7, position.z() / mm);
  analysisMan->FillNtupleDColumn(1, 8, aTrack->GetGlobalTime() / ns);
  analysisMan->AddNtupleRow(1);
  trackInfo->ClearDecayBetaSource();
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
void TrackingAction::PostUserTrackingAction(const G4Track* aTrack)
{
  G4TrackVector* secondaries = fpTrackingManager->GimmeSecondaries();
  if (secondaries) {
    auto info = (TrackInformation*)(aTrack->GetUserInformation());
    const G4String parentName = aTrack->GetDefinition()->GetParticleName();
    G4Track* betaCandidate = nullptr;
    G4String betaCreatorName = "";
    size_t nSeco = secondaries->size();
    if (nSeco > 0) {
      for (size_t i = 0; i < nSeco; ++i) {
        auto secondary = (*secondaries)[i];
        const auto creator = secondary->GetCreatorProcess();
        const G4String creatorName = creator ? creator->GetProcessName() : "";
        const G4bool isDecayElectron =
          secondary->GetDefinition()->GetParticleName() == "e-"
          && creatorName.find("RadioactiveDecay") != G4String::npos;
        if (isDecayElectron
            && (!betaCandidate || secondary->GetKineticEnergy() > betaCandidate->GetKineticEnergy()))
        {
          betaCandidate = secondary;
          betaCreatorName = creatorName;
        }
      }

      for (size_t i = 0; i < nSeco; ++i) {
        auto secondary = (*secondaries)[i];
        auto infoNew = new TrackInformation(info);
        infoNew->ClearDecayBetaSource();
        if (secondary == betaCandidate) {
          infoNew->SetDecayBetaSource(parentName, betaCreatorName);
        }
        secondary->SetUserInformation(infoNew);
      }
    }
  }
}
