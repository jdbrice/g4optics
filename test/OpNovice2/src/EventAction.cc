#include "EventAction.hh"

#include "Run.hh"

#include "G4AnalysisManager.hh"
#include "G4Event.hh"
#include "G4PrimaryVertex.hh"
#include "G4RunManager.hh"
#include "G4SystemOfUnits.hh"
#include "G4ThreeVector.hh"

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
  }

  const G4int generatedOptical = run->GetEventGeneratedOpticalCount();
  const G4int scintillation = run->GetEventScintillationCount();
  const G4int sipmDetected = run->GetEventSiPMDetectionCount();
  const G4double collectionEfficiency =
    generatedOptical > 0 ? G4double(sipmDetected) / G4double(generatedOptical) : 0.;

  auto analysisMan = G4AnalysisManager::Instance();
  analysisMan->FillNtupleIColumn(0, event->GetEventID());
  analysisMan->FillNtupleDColumn(1, primaryPosition.x() / mm);
  analysisMan->FillNtupleDColumn(2, primaryPosition.y() / mm);
  analysisMan->FillNtupleDColumn(3, primaryPosition.z() / mm);
  analysisMan->FillNtupleIColumn(4, generatedOptical);
  analysisMan->FillNtupleIColumn(5, scintillation);
  analysisMan->FillNtupleIColumn(6, sipmDetected);
  analysisMan->FillNtupleDColumn(7, collectionEfficiency);
  analysisMan->AddNtupleRow();
}
