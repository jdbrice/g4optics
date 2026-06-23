#include "EventAction.hh"

#include "Run.hh"

#include "G4AnalysisManager.hh"
#include "G4Event.hh"
#include "G4PrimaryVertex.hh"
#include "G4RunManager.hh"
#include "G4SystemOfUnits.hh"
#include "G4ThreeVector.hh"

#include <limits>

void EventAction::BeginOfEventAction(const G4Event*)
{
  auto run = static_cast<Run*>(G4RunManager::GetRunManager()->GetNonConstCurrentRun());
  if (run) {
    run->BeginEvent();
  }
}

void EventAction::EndOfEventAction(const G4Event* event)
{
  auto run = static_cast<Run*>(G4RunManager::GetRunManager()->GetNonConstCurrentRun());
  if (!run) {
    return;
  }

  G4ThreeVector primaryPosition;
  if (event->GetNumberOfPrimaryVertex() > 0) {
    primaryPosition = event->GetPrimaryVertex(0)->GetPosition();
    run->AddShootPosition(primaryPosition);
  }

  const G4bool hitValid = run->HasEventHitPosition();
  const G4ThreeVector hitPosition = run->GetEventHitPosition();
  const G4bool scintCentroidValid = run->HasEventScintillationCentroid();
  const G4ThreeVector scintCentroid = run->GetEventScintillationCentroid();
  if (scintCentroidValid) {
    run->AddScintillationCentroid(scintCentroid);
  }

  const G4int generatedOptical = run->GetEventGeneratedOpticalCount();
  const G4int scintillation = run->GetEventScintillationCount();
  const G4int sipmDetected = run->GetEventSiPMDetectionCount();
  const G4double collectionEfficiency =
    generatedOptical > 0 ? G4double(sipmDetected) / G4double(generatedOptical) : 0.;
  const G4double missingPosition = std::numeric_limits<G4double>::quiet_NaN();

  auto analysisMan = G4AnalysisManager::Instance();
  analysisMan->FillNtupleIColumn(0, event->GetEventID());
  analysisMan->FillNtupleDColumn(1, primaryPosition.x() / mm);
  analysisMan->FillNtupleDColumn(2, primaryPosition.y() / mm);
  analysisMan->FillNtupleDColumn(3, primaryPosition.z() / mm);
  analysisMan->FillNtupleIColumn(4, hitValid ? 1 : 0);
  analysisMan->FillNtupleDColumn(5, hitValid ? hitPosition.x() / mm : missingPosition);
  analysisMan->FillNtupleDColumn(6, hitValid ? hitPosition.y() / mm : missingPosition);
  analysisMan->FillNtupleDColumn(7, hitValid ? hitPosition.z() / mm : missingPosition);
  analysisMan->FillNtupleIColumn(8, scintCentroidValid ? 1 : 0);
  analysisMan->FillNtupleDColumn(9, scintCentroidValid ? scintCentroid.x() / mm : missingPosition);
  analysisMan->FillNtupleDColumn(10, scintCentroidValid ? scintCentroid.y() / mm : missingPosition);
  analysisMan->FillNtupleDColumn(11, scintCentroidValid ? scintCentroid.z() / mm : missingPosition);
  analysisMan->FillNtupleIColumn(12, generatedOptical);
  analysisMan->FillNtupleIColumn(13, scintillation);
  analysisMan->FillNtupleIColumn(14, sipmDetected);
  analysisMan->FillNtupleDColumn(15, collectionEfficiency);
  analysisMan->AddNtupleRow();
}
