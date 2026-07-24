#include "EventAction.hh"

#include "DetectorConstruction.hh"
#include "Run.hh"

#include "G4AnalysisManager.hh"
#include "G4Event.hh"
#include "G4PrimaryParticle.hh"
#include "G4PrimaryVertex.hh"
#include "G4RunManager.hh"
#include "G4SystemOfUnits.hh"
#include "G4ThreeVector.hh"

#include <cmath>
#include <limits>

void EventAction::BeginOfEventAction(const G4Event* event)
{
  auto run = static_cast<Run*>(G4RunManager::GetRunManager()->GetNonConstCurrentRun());
  if (run) {
    run->BeginEvent(event ? event->GetEventID() : -1);
  }
}

void EventAction::EndOfEventAction(const G4Event* event)
{
  auto run = static_cast<Run*>(G4RunManager::GetRunManager()->GetNonConstCurrentRun());
  if (!run) {
    return;
  }

  G4ThreeVector primaryPosition;
  G4double primaryEnergy = std::numeric_limits<G4double>::quiet_NaN();
  if (event->GetNumberOfPrimaryVertex() > 0) {
    auto primaryVertex = event->GetPrimaryVertex(0);
    primaryPosition = primaryVertex->GetPosition();
    run->AddShootPosition(primaryPosition);
    auto primaryParticle = primaryVertex->GetPrimary();
    if (primaryParticle) {
      primaryEnergy = primaryParticle->GetKineticEnergy();
      if (std::isfinite(primaryEnergy)) {
        run->AddPrimaryKineticEnergy(primaryEnergy);
      }
    }
  }

  const G4bool hitValid = run->HasEventHitPosition();
  const G4ThreeVector hitPosition = run->GetEventHitPosition();
  const G4bool scintCentroidValid = run->HasEventScintillationCentroid();
  const G4ThreeVector scintCentroid = run->GetEventScintillationCentroid();
  if (scintCentroidValid) {
    run->AddScintillationCentroid(scintCentroid);
  }

  const G4int generatedOptical = run->GetEventGeneratedOpticalCount();
  const G4int cerenkov = run->GetEventCerenkovCount();
  const G4int scintillation = run->GetEventScintillationCount();
  const G4int sipmDetected = run->GetEventSiPMDetectionCount();
  const G4bool collectionEfficiencyValid = generatedOptical > 0;
  const G4double collectionEfficiency =
    collectionEfficiencyValid
      ? G4double(sipmDetected) / G4double(generatedOptical)
      : std::numeric_limits<G4double>::quiet_NaN();
  const G4double missingPosition = std::numeric_limits<G4double>::quiet_NaN();

  const G4double steelEnergyDeposit = run->GetEventSteelEnergyDeposit();
  const G4int neutronElasticCount = run->GetEventPrimaryNeutronElasticCount();
  const G4int neutronInelasticCount = run->GetEventPrimaryNeutronInelasticCount();
  const G4int neutronCaptureCount = run->GetEventPrimaryNeutronCaptureCount();
  const G4bool neutronTileEntryValid = run->HasEventPrimaryNeutronTileEntry();
  const G4ThreeVector neutronTileEntryPosition =
    run->GetEventPrimaryNeutronTileEntryPosition();

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
  analysisMan->FillNtupleDColumn(16, primaryEnergy / MeV);
  analysisMan->FillNtupleIColumn(17, collectionEfficiencyValid ? 1 : 0);
  analysisMan->FillNtupleIColumn(18, cerenkov);
  analysisMan->FillNtupleDColumn(19, steelEnergyDeposit / MeV);
  analysisMan->FillNtupleIColumn(20, neutronElasticCount);
  analysisMan->FillNtupleIColumn(21, neutronInelasticCount);
  analysisMan->FillNtupleIColumn(22, neutronCaptureCount);
  analysisMan->FillNtupleIColumn(23, neutronElasticCount > 0 ? 1 : 0);
  analysisMan->FillNtupleIColumn(24, neutronInelasticCount > 0 ? 1 : 0);
  analysisMan->FillNtupleIColumn(25, neutronCaptureCount > 0 ? 1 : 0);
  analysisMan->FillNtupleIColumn(
    26,
    neutronElasticCount > 0 || neutronInelasticCount > 0 || neutronCaptureCount > 0
      ? 1
      : 0);
  analysisMan->FillNtupleIColumn(27, run->GetEventChargedTileEntryCount());
  analysisMan->FillNtupleDColumn(
    28, run->GetEventChargedTileEntryKineticEnergy() / MeV);
  analysisMan->FillNtupleIColumn(29, run->GetEventElectronTileEntryCount());
  analysisMan->FillNtupleDColumn(
    30, run->GetEventElectronTileEntryKineticEnergy() / MeV);
  analysisMan->FillNtupleIColumn(31, run->GetEventProtonTileEntryCount());
  analysisMan->FillNtupleDColumn(
    32, run->GetEventProtonTileEntryKineticEnergy() / MeV);
  analysisMan->FillNtupleIColumn(33, run->GetEventOtherChargedTileEntryCount());
  analysisMan->FillNtupleDColumn(
    34, run->GetEventOtherChargedTileEntryKineticEnergy() / MeV);
  analysisMan->FillNtupleIColumn(35, neutronTileEntryValid ? 1 : 0);
  analysisMan->FillNtupleDColumn(
    36, neutronTileEntryValid ? neutronTileEntryPosition.x() / mm : missingPosition);
  analysisMan->FillNtupleDColumn(
    37, neutronTileEntryValid ? neutronTileEntryPosition.y() / mm : missingPosition);
  analysisMan->FillNtupleDColumn(
    38, neutronTileEntryValid ? neutronTileEntryPosition.z() / mm : missingPosition);
  analysisMan->FillNtupleDColumn(39, run->GetEventTileEnergyDeposit() / MeV);
  analysisMan->FillNtupleDColumn(
    40, run->GetEventElectronTileEnergyDeposit() / MeV);
  analysisMan->FillNtupleDColumn(
    41, run->GetEventProtonTileEnergyDeposit() / MeV);
  analysisMan->FillNtupleDColumn(
    42, run->GetEventOtherChargedTileEnergyDeposit() / MeV);
  analysisMan->FillNtupleDColumn(
    43, run->GetEventNeutralTileEnergyDeposit() / MeV);
  analysisMan->FillNtupleIColumn(44, run->GetEventSiPMDetectionCount(0));
  analysisMan->FillNtupleIColumn(45, run->GetEventSiPMDetectionCount(1));
  analysisMan->FillNtupleIColumn(46, run->GetEventSiPMDetectionCount(2));
  analysisMan->FillNtupleIColumn(47, run->GetEventSiPMDetectionCount(3));
  analysisMan->AddNtupleRow();

  const auto detector = static_cast<const DetectorConstruction*>(
    G4RunManager::GetRunManager()->GetUserDetectorConstruction());
  if (detector && detector->IsStackEnabled()) {
    for (G4int layer = 0; layer < Run::kStackLayerCount; ++layer) {
      const auto& record = run->GetEventStackLayer(layer);
      const G4bool entryValid = record.primaryNeutronTileEntryValid;
      const auto& entry = record.primaryNeutronTileEntryPosition;
      analysisMan->FillNtupleIColumn(2, 0, event->GetEventID());
      analysisMan->FillNtupleIColumn(2, 1, layer);
      analysisMan->FillNtupleIColumn(2, 2, record.generatedOptical);
      analysisMan->FillNtupleIColumn(2, 3, record.scintillation);
      analysisMan->FillNtupleIColumn(2, 4, record.cerenkov);
      analysisMan->FillNtupleIColumn(2, 5, record.sensorAllOrigin[0]);
      analysisMan->FillNtupleIColumn(2, 6, record.sensorAllOrigin[1]);
      analysisMan->FillNtupleIColumn(2, 7, record.sensorLocalOrigin[0]);
      analysisMan->FillNtupleIColumn(2, 8, record.sensorLocalOrigin[1]);
      analysisMan->FillNtupleDColumn(2, 9, record.steelEnergyDeposit / MeV);
      analysisMan->FillNtupleIColumn(2, 10, record.neutronElasticCount);
      analysisMan->FillNtupleIColumn(2, 11, record.neutronInelasticCount);
      analysisMan->FillNtupleIColumn(2, 12, record.neutronCaptureCount);
      analysisMan->FillNtupleIColumn(2, 13, record.chargedEntryCount);
      analysisMan->FillNtupleDColumn(2, 14, record.chargedEntryKineticEnergy / MeV);
      analysisMan->FillNtupleIColumn(2, 15, record.electronEntryCount);
      analysisMan->FillNtupleDColumn(2, 16, record.electronEntryKineticEnergy / MeV);
      analysisMan->FillNtupleIColumn(2, 17, record.protonEntryCount);
      analysisMan->FillNtupleDColumn(2, 18, record.protonEntryKineticEnergy / MeV);
      analysisMan->FillNtupleIColumn(2, 19, record.otherChargedEntryCount);
      analysisMan->FillNtupleDColumn(2, 20, record.otherChargedEntryKineticEnergy / MeV);
      analysisMan->FillNtupleIColumn(2, 21, entryValid ? 1 : 0);
      analysisMan->FillNtupleDColumn(2, 22, entryValid ? entry.x() / mm : missingPosition);
      analysisMan->FillNtupleDColumn(2, 23, entryValid ? entry.y() / mm : missingPosition);
      analysisMan->FillNtupleDColumn(2, 24, entryValid ? entry.z() / mm : missingPosition);
      analysisMan->FillNtupleDColumn(2, 25, record.tileEnergyDeposit / MeV);
      analysisMan->FillNtupleDColumn(2, 26, record.electronTileEnergyDeposit / MeV);
      analysisMan->FillNtupleDColumn(2, 27, record.protonTileEnergyDeposit / MeV);
      analysisMan->FillNtupleDColumn(2, 28, record.otherChargedTileEnergyDeposit / MeV);
      analysisMan->FillNtupleDColumn(2, 29, record.neutralTileEnergyDeposit / MeV);
      analysisMan->AddNtupleRow(2);
    }

    for (G4int origin = -1; origin < Run::kStackLayerCount; ++origin) {
      for (G4int globalCopy = 0; globalCopy < Run::kStackSensorCount; ++globalCopy) {
        const G4int detected = run->GetEventStackTransfer(origin, globalCopy);
        if (detected <= 0) {
          continue;
        }
        const G4int destination = globalCopy / Run::kStackSensorsPerLayer;
        const G4int localSensor = globalCopy % Run::kStackSensorsPerLayer;
        analysisMan->FillNtupleIColumn(3, 0, event->GetEventID());
        analysisMan->FillNtupleIColumn(3, 1, origin);
        analysisMan->FillNtupleIColumn(3, 2, destination);
        analysisMan->FillNtupleIColumn(3, 3, localSensor);
        analysisMan->FillNtupleIColumn(3, 4, globalCopy);
        analysisMan->FillNtupleIColumn(3, 5, detected);
        analysisMan->AddNtupleRow(3);
      }
    }
  }

  run->CommitEventStatistics();
}
