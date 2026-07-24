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
/// \file optical/OpNovice2/src/Run.cc
/// \brief Implementation of the Run class
//
//
//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......

#include "Run.hh"

#include "DetectorConstruction.hh"
#include "HistoManager.hh"

#include "G4OpBoundaryProcess.hh"
#include "G4SystemOfUnits.hh"
#include "G4UnitsTable.hh"

#include <algorithm>
#include <cmath>
#include <numeric>

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
Run::Run() : G4Run()
{
  fBoundaryProcs.assign(43, 0);
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
void Run::BeginEvent(G4int eventID)
{
  fCurrentEventID = eventID;
  fEventGeneratedOpticalCount = 0;
  fEventCerenkovCount = 0;
  fEventScintCount = 0;
  fEventSiPMDetectionCount = 0;
  fEventSiPMDetectionCounts.fill(0);
  fEventStackLayers.fill(StackLayerEventRecord{});
  for (auto& origins : fEventStackTransfers) {
    origins.fill(0);
  }
  for (auto& trackIDs : fEventStackChargedTileEntryTrackIDs) {
    trackIDs.clear();
  }
  fEventHitValid = false;
  fEventHitPosition = G4ThreeVector();
  fEventScintPositionSum = G4ThreeVector();
  fEventSteelEnergyDeposit = 0.;
  fEventPrimaryNeutronElasticCount = 0;
  fEventPrimaryNeutronInelasticCount = 0;
  fEventPrimaryNeutronCaptureCount = 0;
  fEventChargedTileEntryTrackIDs.clear();
  fEventChargedTileEntryCount = 0;
  fEventChargedTileEntryKineticEnergy = 0.;
  fEventElectronTileEntryCount = 0;
  fEventElectronTileEntryKineticEnergy = 0.;
  fEventProtonTileEntryCount = 0;
  fEventProtonTileEntryKineticEnergy = 0.;
  fEventOtherChargedTileEntryCount = 0;
  fEventOtherChargedTileEntryKineticEnergy = 0.;
  fEventPrimaryNeutronTileEntryValid = false;
  fEventPrimaryNeutronTileEntryPosition = G4ThreeVector();
  fEventTileEnergyDeposit = 0.;
  fEventElectronTileEnergyDeposit = 0.;
  fEventProtonTileEnergyDeposit = 0.;
  fEventOtherChargedTileEnergyDeposit = 0.;
  fEventNeutralTileEnergyDeposit = 0.;
  fEventStatisticsCommitted = false;
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
void Run::CommitEventStatistics()
{
  if (fEventStatisticsCommitted) {
    return;
  }

  fEventStatisticsCommitted = true;
  fCommittedEventCount += 1;
  fSteelEnergyDepositMoments.Add(fEventSteelEnergyDeposit);
  fPrimaryNeutronElasticMoments.Add(fEventPrimaryNeutronElasticCount);
  fPrimaryNeutronInelasticMoments.Add(fEventPrimaryNeutronInelasticCount);
  fPrimaryNeutronCaptureMoments.Add(fEventPrimaryNeutronCaptureCount);
  fPrimaryNeutronInteractionMoments.Add(
    fEventPrimaryNeutronElasticCount > 0 ||
        fEventPrimaryNeutronInelasticCount > 0 ||
        fEventPrimaryNeutronCaptureCount > 0
      ? 1.
      : 0.);
  fChargedTileEntryCountMoments.Add(fEventChargedTileEntryCount);
  fChargedTileEntryKineticEnergyMoments.Add(fEventChargedTileEntryKineticEnergy);
  fElectronTileEntryCountMoments.Add(fEventElectronTileEntryCount);
  fElectronTileEntryKineticEnergyMoments.Add(fEventElectronTileEntryKineticEnergy);
  fProtonTileEntryCountMoments.Add(fEventProtonTileEntryCount);
  fProtonTileEntryKineticEnergyMoments.Add(fEventProtonTileEntryKineticEnergy);
  fOtherChargedTileEntryCountMoments.Add(fEventOtherChargedTileEntryCount);
  fOtherChargedTileEntryKineticEnergyMoments.Add(
    fEventOtherChargedTileEntryKineticEnergy);
  fPrimaryNeutronTileEntryMoments.Add(
    fEventPrimaryNeutronTileEntryValid ? 1. : 0.);
  fTileEnergyDepositMoments.Add(fEventTileEnergyDeposit);
  fElectronTileEnergyDepositMoments.Add(fEventElectronTileEnergyDeposit);
  fProtonTileEnergyDepositMoments.Add(fEventProtonTileEnergyDeposit);
  fOtherChargedTileEnergyDepositMoments.Add(fEventOtherChargedTileEnergyDeposit);
  fNeutralTileEnergyDepositMoments.Add(fEventNeutralTileEnergyDeposit);
  fGeneratedOpticalMoments.Add(fEventGeneratedOpticalCount);
  fScintillationMoments.Add(fEventScintCount);
  fSiPMDetectionMoments.Add(fEventSiPMDetectionCount);
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
void Run::AddPrimaryKineticEnergy(G4double energy)
{
  fPrimaryEnergyCount += 1;
  fPrimaryEnergySum += energy;
  fPrimaryEnergySum2 += energy * energy;
  fPrimaryEnergyMin = std::min(fPrimaryEnergyMin, energy);
  fPrimaryEnergyMax = std::max(fPrimaryEnergyMax, energy);
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
void Run::AddDecayBetaEnergy(G4double energy)
{
  fDecayBetaCount += 1;
  fDecayBetaEnergySum += energy;
  fDecayBetaEnergySum2 += energy * energy;
  fDecayBetaEnergyMin = std::min(fDecayBetaEnergyMin, energy);
  fDecayBetaEnergyMax = std::max(fDecayBetaEnergyMax, energy);
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
void Run::AddShootPosition(const G4ThreeVector& pos)
{
  fShootPositionCount += 1;
  fShootPositionSum += pos;
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
void Run::SetPrimaryHitPosition(const G4ThreeVector& pos)
{
  if (fEventHitValid) {
    return;
  }
  fEventHitValid = true;
  fEventHitPosition = pos;
  fHitPositionCount += 1;
  fHitPositionSum += pos;
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
void Run::SetPrimaryNeutronTileEntry(const G4ThreeVector& pos, G4int layer)
{
  if (!fEventPrimaryNeutronTileEntryValid) {
    fEventPrimaryNeutronTileEntryValid = true;
    fEventPrimaryNeutronTileEntryPosition = pos;
  }
  if (layer >= 0 && layer < kStackLayerCount) {
    auto& record = fEventStackLayers[static_cast<std::size_t>(layer)];
    if (!record.primaryNeutronTileEntryValid) {
      record.primaryNeutronTileEntryValid = true;
      record.primaryNeutronTileEntryPosition = pos;
    }
  }
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
void Run::AddSteelEnergyDeposit(G4double energy, G4int layer)
{
  if (energy > 0.) {
    fEventSteelEnergyDeposit += energy;
    if (layer >= 0 && layer < kStackLayerCount) {
      fEventStackLayers[static_cast<std::size_t>(layer)].steelEnergyDeposit += energy;
    }
  }
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
void Run::AddPrimaryNeutronElasticInteraction(G4int layer)
{
  fEventPrimaryNeutronElasticCount += 1;
  if (layer >= 0 && layer < kStackLayerCount) {
    fEventStackLayers[static_cast<std::size_t>(layer)].neutronElasticCount += 1;
  }
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
void Run::AddPrimaryNeutronInelasticInteraction(G4int layer)
{
  fEventPrimaryNeutronInelasticCount += 1;
  if (layer >= 0 && layer < kStackLayerCount) {
    fEventStackLayers[static_cast<std::size_t>(layer)].neutronInelasticCount += 1;
  }
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
void Run::AddPrimaryNeutronCaptureInteraction(G4int layer)
{
  fEventPrimaryNeutronCaptureCount += 1;
  if (layer >= 0 && layer < kStackLayerCount) {
    fEventStackLayers[static_cast<std::size_t>(layer)].neutronCaptureCount += 1;
  }
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
G4bool Run::RecordChargedTileEntry(G4int trackID,
                                   G4int pdgEncoding,
                                   G4double charge,
                                   G4double kineticEnergy,
                                   G4int layer)
{
  if (charge == 0.) {
    return false;
  }

  const G4long globalKey = layer >= 0
    ? (static_cast<G4long>(layer) << 32) | static_cast<unsigned int>(trackID)
    : static_cast<G4long>(trackID);
  if (!fEventChargedTileEntryTrackIDs.insert(globalKey).second) {
    return false;
  }

  StackLayerEventRecord* layerRecord = nullptr;
  if (layer >= 0 && layer < kStackLayerCount) {
    auto& layerIDs =
      fEventStackChargedTileEntryTrackIDs[static_cast<std::size_t>(layer)];
    if (!layerIDs.insert(trackID).second) {
      return false;
    }
    layerRecord = &fEventStackLayers[static_cast<std::size_t>(layer)];
    layerRecord->chargedEntryCount += 1;
    layerRecord->chargedEntryKineticEnergy += kineticEnergy;
  }

  fEventChargedTileEntryCount += 1;
  fEventChargedTileEntryKineticEnergy += kineticEnergy;

  const G4int absPdg = std::abs(pdgEncoding);
  if (absPdg == 11) {
    fEventElectronTileEntryCount += 1;
    fEventElectronTileEntryKineticEnergy += kineticEnergy;
    if (layerRecord) {
      layerRecord->electronEntryCount += 1;
      layerRecord->electronEntryKineticEnergy += kineticEnergy;
    }
  }
  else if (pdgEncoding == 2212) {
    fEventProtonTileEntryCount += 1;
    fEventProtonTileEntryKineticEnergy += kineticEnergy;
    if (layerRecord) {
      layerRecord->protonEntryCount += 1;
      layerRecord->protonEntryKineticEnergy += kineticEnergy;
    }
  }
  else {
    fEventOtherChargedTileEntryCount += 1;
    fEventOtherChargedTileEntryKineticEnergy += kineticEnergy;
    if (layerRecord) {
      layerRecord->otherChargedEntryCount += 1;
      layerRecord->otherChargedEntryKineticEnergy += kineticEnergy;
    }
  }

  return true;
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
void Run::AddTileEnergyDeposit(G4int pdgEncoding,
                               G4double charge,
                               G4double energy,
                               G4int layer)
{
  if (energy <= 0.) {
    return;
  }

  fEventTileEnergyDeposit += energy;
  StackLayerEventRecord* layerRecord = nullptr;
  if (layer >= 0 && layer < kStackLayerCount) {
    layerRecord = &fEventStackLayers[static_cast<std::size_t>(layer)];
    layerRecord->tileEnergyDeposit += energy;
  }
  const G4int absPdg = std::abs(pdgEncoding);
  if (absPdg == 11) {
    fEventElectronTileEnergyDeposit += energy;
    if (layerRecord) layerRecord->electronTileEnergyDeposit += energy;
  }
  else if (pdgEncoding == 2212) {
    fEventProtonTileEnergyDeposit += energy;
    if (layerRecord) layerRecord->protonTileEnergyDeposit += energy;
  }
  else if (charge != 0.) {
    fEventOtherChargedTileEnergyDeposit += energy;
    if (layerRecord) layerRecord->otherChargedTileEnergyDeposit += energy;
  }
  else {
    fEventNeutralTileEnergyDeposit += energy;
    if (layerRecord) layerRecord->neutralTileEnergyDeposit += energy;
  }
}

void Run::AddSiPMDetection(G4int sensorIndex, G4int originLayer)
{
  fSiPMDetectionCount += 1;
  fEventSiPMDetectionCount += 1;
  if (sensorIndex >= 0 && sensorIndex < 4) {
    const auto index = static_cast<std::size_t>(sensorIndex);
    fSiPMDetectionCounts[index] += 1;
    fEventSiPMDetectionCounts[index] += 1;
  }
  if (sensorIndex < 0 || sensorIndex >= kStackSensorCount) {
    return;
  }

  const G4int destinationLayer = sensorIndex / kStackSensorsPerLayer;
  const G4int localSensor = sensorIndex % kStackSensorsPerLayer;
  auto& destination =
    fEventStackLayers[static_cast<std::size_t>(destinationLayer)];
  destination.sensorAllOrigin[static_cast<std::size_t>(localSensor)] += 1;
  if (originLayer == destinationLayer) {
    destination.sensorLocalOrigin[static_cast<std::size_t>(localSensor)] += 1;
  }
  const G4int originIndex = originLayer >= 0 && originLayer < kStackLayerCount
    ? originLayer
    : kUnknownOriginIndex;
  fEventStackTransfers[static_cast<std::size_t>(originIndex)]
                      [static_cast<std::size_t>(sensorIndex)] += 1;
}

const StackLayerEventRecord& Run::GetEventStackLayer(G4int layer) const
{
  static const StackLayerEventRecord empty;
  return layer >= 0 && layer < kStackLayerCount
    ? fEventStackLayers[static_cast<std::size_t>(layer)]
    : empty;
}

G4int Run::GetEventStackTransfer(G4int originLayer, G4int sensorCopy) const
{
  const G4int originIndex = originLayer >= 0 && originLayer < kStackLayerCount
    ? originLayer
    : kUnknownOriginIndex;
  if (sensorCopy < 0 || sensorCopy >= kStackSensorCount) {
    return 0;
  }
  return fEventStackTransfers[static_cast<std::size_t>(originIndex)]
                             [static_cast<std::size_t>(sensorCopy)];
}

G4int Run::GetEventUnknownOriginDetections() const
{
  G4int total = 0;
  for (const auto value :
       fEventStackTransfers[static_cast<std::size_t>(kUnknownOriginIndex)]) {
    total += value;
  }
  return total;
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
void Run::AddScintillationCentroid(const G4ThreeVector& pos)
{
  fScintCentroidCount += 1;
  fScintCentroidSum += pos;
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
G4ThreeVector Run::GetEventScintillationCentroid() const
{
  if (fEventScintCount == 0) {
    return G4ThreeVector();
  }
  return fEventScintPositionSum / G4double(fEventScintCount);
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
G4ThreeVector Run::GetMeanShootPosition() const
{
  if (fShootPositionCount == 0) {
    return G4ThreeVector();
  }
  return fShootPositionSum / G4double(fShootPositionCount);
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
G4ThreeVector Run::GetMeanHitPosition() const
{
  if (fHitPositionCount == 0) {
    return G4ThreeVector();
  }
  return fHitPositionSum / G4double(fHitPositionCount);
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
G4ThreeVector Run::GetMeanScintillationCentroid() const
{
  if (fScintCentroidCount == 0) {
    return G4ThreeVector();
  }
  return fScintCentroidSum / G4double(fScintCentroidCount);
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
G4double Run::GetPrimaryKineticEnergyMean() const
{
  if (fPrimaryEnergyCount == 0) {
    return 0.;
  }
  return fPrimaryEnergySum / G4double(fPrimaryEnergyCount);
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
G4double Run::GetPrimaryKineticEnergyRms() const
{
  if (fPrimaryEnergyCount == 0) {
    return 0.;
  }
  const G4double mean = GetPrimaryKineticEnergyMean();
  const G4double mean2 = fPrimaryEnergySum2 / G4double(fPrimaryEnergyCount);
  return std::sqrt(std::max(0., mean2 - mean * mean));
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
G4double Run::GetDecayBetaEnergyMean() const
{
  if (fDecayBetaCount == 0) {
    return 0.;
  }
  return fDecayBetaEnergySum / G4double(fDecayBetaCount);
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
G4double Run::GetDecayBetaEnergyRms() const
{
  if (fDecayBetaCount == 0) {
    return 0.;
  }
  const G4double mean = GetDecayBetaEnergyMean();
  const G4double mean2 = fDecayBetaEnergySum2 / G4double(fDecayBetaCount);
  return std::sqrt(std::max(0., mean2 - mean * mean));
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
G4double Run::GetEventMean(const EventMoments& moments) const
{
  if (fCommittedEventCount == 0) {
    return std::numeric_limits<G4double>::quiet_NaN();
  }
  return moments.sum / G4double(fCommittedEventCount);
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
G4double Run::GetEventRms(const EventMoments& moments) const
{
  if (fCommittedEventCount == 0) {
    return std::numeric_limits<G4double>::quiet_NaN();
  }
  const G4double mean = GetEventMean(moments);
  const G4double mean2 = moments.sum2 / G4double(fCommittedEventCount);
  return std::sqrt(std::max(0., mean2 - mean * mean));
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
G4double Run::GetEventStandardError(const EventMoments& moments) const
{
  if (fCommittedEventCount == 0) {
    return std::numeric_limits<G4double>::quiet_NaN();
  }
  return GetEventRms(moments) / std::sqrt(G4double(fCommittedEventCount));
}

G4double Run::GetSteelEnergyDepositSum() const
{
  return fSteelEnergyDepositMoments.sum;
}

G4double Run::GetSteelEnergyDepositMean() const
{
  return GetEventMean(fSteelEnergyDepositMoments);
}

G4double Run::GetSteelEnergyDepositRms() const
{
  return GetEventRms(fSteelEnergyDepositMoments);
}

G4double Run::GetSteelEnergyDepositStandardError() const
{
  return GetEventStandardError(fSteelEnergyDepositMoments);
}

G4long Run::GetSteelEnergyDepositNonzeroEventCount() const
{
  return fSteelEnergyDepositMoments.nonzeroCount;
}

G4long Run::GetPrimaryNeutronElasticInteractionCount() const
{
  return static_cast<G4long>(fPrimaryNeutronElasticMoments.sum);
}

G4long Run::GetPrimaryNeutronInelasticInteractionCount() const
{
  return static_cast<G4long>(fPrimaryNeutronInelasticMoments.sum);
}

G4long Run::GetPrimaryNeutronCaptureInteractionCount() const
{
  return static_cast<G4long>(fPrimaryNeutronCaptureMoments.sum);
}

G4long Run::GetPrimaryNeutronElasticEventCount() const
{
  return fPrimaryNeutronElasticMoments.nonzeroCount;
}

G4long Run::GetPrimaryNeutronInelasticEventCount() const
{
  return fPrimaryNeutronInelasticMoments.nonzeroCount;
}

G4long Run::GetPrimaryNeutronCaptureEventCount() const
{
  return fPrimaryNeutronCaptureMoments.nonzeroCount;
}

G4long Run::GetPrimaryNeutronInteractionEventCount() const
{
  return fPrimaryNeutronInteractionMoments.nonzeroCount;
}

G4long Run::GetChargedTileEntryCount() const
{
  return static_cast<G4long>(fChargedTileEntryCountMoments.sum);
}

G4long Run::GetChargedTileEntryEventCount() const
{
  return fChargedTileEntryCountMoments.nonzeroCount;
}

G4double Run::GetChargedTileEntryKineticEnergySum() const
{
  return fChargedTileEntryKineticEnergyMoments.sum;
}

G4long Run::GetElectronTileEntryCount() const
{
  return static_cast<G4long>(fElectronTileEntryCountMoments.sum);
}

G4double Run::GetElectronTileEntryKineticEnergySum() const
{
  return fElectronTileEntryKineticEnergyMoments.sum;
}

G4long Run::GetProtonTileEntryCount() const
{
  return static_cast<G4long>(fProtonTileEntryCountMoments.sum);
}

G4double Run::GetProtonTileEntryKineticEnergySum() const
{
  return fProtonTileEntryKineticEnergyMoments.sum;
}

G4long Run::GetOtherChargedTileEntryCount() const
{
  return static_cast<G4long>(fOtherChargedTileEntryCountMoments.sum);
}

G4double Run::GetOtherChargedTileEntryKineticEnergySum() const
{
  return fOtherChargedTileEntryKineticEnergyMoments.sum;
}

G4long Run::GetPrimaryNeutronTileEntryEventCount() const
{
  return fPrimaryNeutronTileEntryMoments.nonzeroCount;
}

G4double Run::GetTileEnergyDepositSum() const
{
  return fTileEnergyDepositMoments.sum;
}

G4double Run::GetElectronTileEnergyDepositSum() const
{
  return fElectronTileEnergyDepositMoments.sum;
}

G4double Run::GetProtonTileEnergyDepositSum() const
{
  return fProtonTileEnergyDepositMoments.sum;
}

G4double Run::GetOtherChargedTileEnergyDepositSum() const
{
  return fOtherChargedTileEnergyDepositMoments.sum;
}

G4double Run::GetNeutralTileEnergyDepositSum() const
{
  return fNeutralTileEnergyDepositMoments.sum;
}

G4double Run::GetTileEnergyDepositMean() const
{
  return GetEventMean(fTileEnergyDepositMoments);
}

G4double Run::GetTileEnergyDepositRms() const
{
  return GetEventRms(fTileEnergyDepositMoments);
}

G4double Run::GetTileEnergyDepositStandardError() const
{
  return GetEventStandardError(fTileEnergyDepositMoments);
}

G4long Run::GetTileEnergyDepositNonzeroEventCount() const
{
  return fTileEnergyDepositMoments.nonzeroCount;
}

G4double Run::GetGeneratedOpticalMean() const
{
  return GetEventMean(fGeneratedOpticalMoments);
}

G4double Run::GetGeneratedOpticalRms() const
{
  return GetEventRms(fGeneratedOpticalMoments);
}

G4double Run::GetGeneratedOpticalStandardError() const
{
  return GetEventStandardError(fGeneratedOpticalMoments);
}

G4long Run::GetGeneratedOpticalNonzeroEventCount() const
{
  return fGeneratedOpticalMoments.nonzeroCount;
}

G4double Run::GetScintillationMean() const
{
  return GetEventMean(fScintillationMoments);
}

G4double Run::GetScintillationRms() const
{
  return GetEventRms(fScintillationMoments);
}

G4double Run::GetScintillationStandardError() const
{
  return GetEventStandardError(fScintillationMoments);
}

G4long Run::GetScintillationNonzeroEventCount() const
{
  return fScintillationMoments.nonzeroCount;
}

G4double Run::GetSiPMDetectionMean() const
{
  return GetEventMean(fSiPMDetectionMoments);
}

G4double Run::GetSiPMDetectionRms() const
{
  return GetEventRms(fSiPMDetectionMoments);
}

G4double Run::GetSiPMDetectionStandardError() const
{
  return GetEventStandardError(fSiPMDetectionMoments);
}

G4long Run::GetSiPMDetectionNonzeroEventCount() const
{
  return fSiPMDetectionMoments.nonzeroCount;
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
void Run::SetPrimary(G4ParticleDefinition* particle, G4double energy, G4bool polarized,
                     G4double polarization, const G4String& electronEnergyMode)
{
  fParticle = particle;
  fEkin = energy;
  fPolarized = polarized;
  fPolarization = polarization;
  fElectronEnergyMode = electronEnergyMode;
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
void Run::Merge(const G4Run* run)
{
  const Run* localRun = static_cast<const Run*>(run);

  // pass information about primary particle
  fParticle = localRun->fParticle;
  fEkin = localRun->fEkin;
  fPolarized = localRun->fPolarized;
  fPolarization = localRun->fPolarization;
  fElectronEnergyMode = localRun->fElectronEnergyMode;

  fCerenkovEnergy += localRun->fCerenkovEnergy;
  fScintEnergy += localRun->fScintEnergy;
  fWLSAbsorptionEnergy += localRun->fWLSAbsorptionEnergy;
  fWLSEmissionEnergy += localRun->fWLSEmissionEnergy;
  fWLS2AbsorptionEnergy += localRun->fWLS2AbsorptionEnergy;
  fWLS2EmissionEnergy += localRun->fWLS2EmissionEnergy;

  fCerenkovCount += localRun->fCerenkovCount;
  fScintCount += localRun->fScintCount;
  fWLSAbsorptionCount += localRun->fWLSAbsorptionCount;
  fWLSEmissionCount += localRun->fWLSEmissionCount;
  fWLS2AbsorptionCount += localRun->fWLS2AbsorptionCount;
  fWLS2EmissionCount += localRun->fWLS2EmissionCount;
  fRayleighCount += localRun->fRayleighCount;

  fTotalSurface += localRun->fTotalSurface;

  fOpAbsorption += localRun->fOpAbsorption;
  fOpAbsorptionPrior += localRun->fOpAbsorptionPrior;

  for (size_t i = 0; i < fBoundaryProcs.size(); ++i) {
    fBoundaryProcs[i] += localRun->fBoundaryProcs[i];
  }

  // SiPM count
  fSiPMDetectionCount += localRun->fSiPMDetectionCount;
  for (std::size_t index = 0; index < fSiPMDetectionCounts.size(); ++index) {
    fSiPMDetectionCounts[index] += localRun->fSiPMDetectionCounts[index];
  }

  fShootPositionCount += localRun->fShootPositionCount;
  fShootPositionSum += localRun->fShootPositionSum;
  fHitPositionCount += localRun->fHitPositionCount;
  fHitPositionSum += localRun->fHitPositionSum;
  fScintCentroidCount += localRun->fScintCentroidCount;
  fScintCentroidSum += localRun->fScintCentroidSum;
  fPrimaryEnergyCount += localRun->fPrimaryEnergyCount;
  fPrimaryEnergySum += localRun->fPrimaryEnergySum;
  fPrimaryEnergySum2 += localRun->fPrimaryEnergySum2;
  fPrimaryEnergyMin = std::min(fPrimaryEnergyMin, localRun->fPrimaryEnergyMin);
  fPrimaryEnergyMax = std::max(fPrimaryEnergyMax, localRun->fPrimaryEnergyMax);
  fDecayBetaCount += localRun->fDecayBetaCount;
  fDecayBetaEnergySum += localRun->fDecayBetaEnergySum;
  fDecayBetaEnergySum2 += localRun->fDecayBetaEnergySum2;
  fDecayBetaEnergyMin = std::min(fDecayBetaEnergyMin, localRun->fDecayBetaEnergyMin);
  fDecayBetaEnergyMax = std::max(fDecayBetaEnergyMax, localRun->fDecayBetaEnergyMax);

  fCommittedEventCount += localRun->fCommittedEventCount;
  fSteelEnergyDepositMoments.Merge(localRun->fSteelEnergyDepositMoments);
  fPrimaryNeutronElasticMoments.Merge(localRun->fPrimaryNeutronElasticMoments);
  fPrimaryNeutronInelasticMoments.Merge(localRun->fPrimaryNeutronInelasticMoments);
  fPrimaryNeutronCaptureMoments.Merge(localRun->fPrimaryNeutronCaptureMoments);
  fPrimaryNeutronInteractionMoments.Merge(
    localRun->fPrimaryNeutronInteractionMoments);
  fChargedTileEntryCountMoments.Merge(localRun->fChargedTileEntryCountMoments);
  fChargedTileEntryKineticEnergyMoments.Merge(
    localRun->fChargedTileEntryKineticEnergyMoments);
  fElectronTileEntryCountMoments.Merge(localRun->fElectronTileEntryCountMoments);
  fElectronTileEntryKineticEnergyMoments.Merge(
    localRun->fElectronTileEntryKineticEnergyMoments);
  fProtonTileEntryCountMoments.Merge(localRun->fProtonTileEntryCountMoments);
  fProtonTileEntryKineticEnergyMoments.Merge(
    localRun->fProtonTileEntryKineticEnergyMoments);
  fOtherChargedTileEntryCountMoments.Merge(
    localRun->fOtherChargedTileEntryCountMoments);
  fOtherChargedTileEntryKineticEnergyMoments.Merge(
    localRun->fOtherChargedTileEntryKineticEnergyMoments);
  fPrimaryNeutronTileEntryMoments.Merge(localRun->fPrimaryNeutronTileEntryMoments);
  fTileEnergyDepositMoments.Merge(localRun->fTileEnergyDepositMoments);
  fElectronTileEnergyDepositMoments.Merge(localRun->fElectronTileEnergyDepositMoments);
  fProtonTileEnergyDepositMoments.Merge(localRun->fProtonTileEnergyDepositMoments);
  fOtherChargedTileEnergyDepositMoments.Merge(
    localRun->fOtherChargedTileEnergyDepositMoments);
  fNeutralTileEnergyDepositMoments.Merge(localRun->fNeutralTileEnergyDepositMoments);
  fGeneratedOpticalMoments.Merge(localRun->fGeneratedOpticalMoments);
  fScintillationMoments.Merge(localRun->fScintillationMoments);
  fSiPMDetectionMoments.Merge(localRun->fSiPMDetectionMoments);

  G4Run::Merge(run);
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
void Run::EndOfRun()
{
  if (numberOfEvent == 0) return;
  auto TotNbofEvents = (G4double)numberOfEvent;

  G4AnalysisManager* analysisMan = G4AnalysisManager::Instance();
  G4int id = analysisMan->GetH1Id("Cerenkov spectrum");
  analysisMan->SetH1XAxisTitle(id, "Energy [eV]");
  analysisMan->SetH1YAxisTitle(id, "Number of photons");

  id = analysisMan->GetH1Id("Scintillation spectrum");
  analysisMan->SetH1XAxisTitle(id, "Energy [eV]");
  analysisMan->SetH1YAxisTitle(id, "Number of photons");

  id = analysisMan->GetH1Id("Scintillation time");
  analysisMan->SetH1XAxisTitle(id, "Creation time [ns]");
  analysisMan->SetH1YAxisTitle(id, "Number of photons");

  id = analysisMan->GetH1Id("WLS abs");
  analysisMan->SetH1XAxisTitle(id, "Energy [eV]");
  analysisMan->SetH1YAxisTitle(id, "Number of photons");

  id = analysisMan->GetH1Id("WLS em");
  analysisMan->SetH1XAxisTitle(id, "Energy [eV]");
  analysisMan->SetH1YAxisTitle(id, "Number of photons");

  id = analysisMan->GetH1Id("WLS time");
  analysisMan->SetH1XAxisTitle(id, "Creation time [ns]");
  analysisMan->SetH1YAxisTitle(id, "Number of photons");

  id = analysisMan->GetH1Id("WLS2 abs");
  analysisMan->SetH1XAxisTitle(id, "Energy [eV]");
  analysisMan->SetH1YAxisTitle(id, "Number of photons");

  id = analysisMan->GetH1Id("WLS2 em");
  analysisMan->SetH1XAxisTitle(id, "Energy [eV]");
  analysisMan->SetH1YAxisTitle(id, "Number of photons");

  id = analysisMan->GetH1Id("WLS2 time");
  analysisMan->SetH1XAxisTitle(id, "Creation time [ns]");
  analysisMan->SetH1YAxisTitle(id, "Number of photons");

  id = analysisMan->GetH1Id("bdry status");
  analysisMan->SetH1XAxisTitle(id, "Status code");
  analysisMan->SetH1YAxisTitle(id, "Number of photons");

  id = analysisMan->GetH1Id("x_backward");
  analysisMan->SetH1XAxisTitle(id, "Direction cosine");
  analysisMan->SetH1YAxisTitle(id, "Number of photons");

  id = analysisMan->GetH1Id("y_backward");
  analysisMan->SetH1XAxisTitle(id, "Direction cosine");
  analysisMan->SetH1YAxisTitle(id, "Number of photons");

  id = analysisMan->GetH1Id("z_backward");
  analysisMan->SetH1XAxisTitle(id, "Direction cosine");
  analysisMan->SetH1YAxisTitle(id, "Number of photons");

  id = analysisMan->GetH1Id("x_forward");
  analysisMan->SetH1XAxisTitle(id, "Direction cosine");
  analysisMan->SetH1YAxisTitle(id, "Number of photons");

  id = analysisMan->GetH1Id("y_forward");
  analysisMan->SetH1XAxisTitle(id, "Direction cosine");
  analysisMan->SetH1YAxisTitle(id, "Number of photons");

  id = analysisMan->GetH1Id("z_forward");
  analysisMan->SetH1XAxisTitle(id, "Direction cosine");
  analysisMan->SetH1YAxisTitle(id, "Number of photons");

  id = analysisMan->GetH1Id("x_fresnel");
  analysisMan->SetH1XAxisTitle(id, "Direction cosine");
  analysisMan->SetH1YAxisTitle(id, "Number of photons");

  id = analysisMan->GetH1Id("y_fresnel");
  analysisMan->SetH1XAxisTitle(id, "Direction cosine");
  analysisMan->SetH1YAxisTitle(id, "Number of photons");

  id = analysisMan->GetH1Id("z_fresnel");
  analysisMan->SetH1XAxisTitle(id, "Direction cosine");
  analysisMan->SetH1YAxisTitle(id, "Number of photons");

  id = analysisMan->GetH1Id("Fresnel reflection");
  analysisMan->SetH1XAxisTitle(id, "Angle [deg]");
  analysisMan->SetH1YAxisTitle(id, "Fraction of photons");

  id = analysisMan->GetH1Id("Fresnel refraction");
  analysisMan->SetH1XAxisTitle(id, "Angle [deg]");
  analysisMan->SetH1YAxisTitle(id, "Fraction of photons");

  id = analysisMan->GetH1Id("Total internal reflection");
  analysisMan->SetH1XAxisTitle(id, "Angle [deg]");
  analysisMan->SetH1YAxisTitle(id, "Fraction of photons");

  id = analysisMan->GetH1Id("Fresnel reflection plus TIR");
  analysisMan->SetH1XAxisTitle(id, "Angle [deg]");
  analysisMan->SetH1YAxisTitle(id, "Fraction of photons");

  id = analysisMan->GetH1Id("Absorption");
  analysisMan->SetH1XAxisTitle(id, "Angle [deg]");
  analysisMan->SetH1YAxisTitle(id, "Fraction of photons");

  id = analysisMan->GetH1Id("Transmitted");
  analysisMan->SetH1XAxisTitle(id, "Angle [deg]");
  analysisMan->SetH1YAxisTitle(id, "Fraction of photons");

  id = analysisMan->GetH1Id("Spike reflection");
  analysisMan->SetH1XAxisTitle(id, "Angle [deg]");
  analysisMan->SetH1YAxisTitle(id, "Fraction of photons");

  const auto det =
    (const DetectorConstruction*)(G4RunManager::GetRunManager()->GetUserDetectorConstruction());

  std::ios::fmtflags mode = G4cout.flags();
  G4int prec = G4cout.precision(2);

  G4cout << "\n    Run Summary\n";
  G4cout << "---------------------------------\n";
  G4cout << "Primary particle was: " << fParticle->GetParticleName();
  if (fElectronEnergyMode == "fixed") {
    G4cout << " with energy " << G4BestUnit(fEkin, "Energy") << "." << G4endl;
  }
  else {
    G4cout << " with " << fElectronEnergyMode
           << " energy sampling; nominal /gun/energy is "
           << G4BestUnit(fEkin, "Energy") << "." << G4endl;
  }
  G4cout << "Number of events: " << numberOfEvent << G4endl;

  G4cout << "Material of world: " << det->GetWorldMaterial()->GetName() << G4endl;
  G4cout << "Material of tank:  " << det->GetTankMaterial()->GetName() << G4endl << G4endl;

  if (fParticle->GetParticleName() != "opticalphoton") {
    G4cout << "Average energy of Cerenkov photons created per event: "
           << (fCerenkovEnergy / eV) / TotNbofEvents << " eV." << G4endl;
    G4cout << "Average number of Cerenkov photons created per event: "
           << fCerenkovCount / TotNbofEvents << G4endl;
    if (fCerenkovCount > 0) {
      G4cout << " Average energy per photon: " << (fCerenkovEnergy / eV) / fCerenkovCount << " eV."
             << G4endl;
    }
    G4cout << "Average energy of scintillation photons created per event: "
           << (fScintEnergy / eV) / TotNbofEvents << " eV." << G4endl;
    G4cout << "Average number of scintillation photons created per event: "
           << fScintCount / TotNbofEvents << G4endl;
    if (fScintCount > 0) {
      G4cout << " Average energy per photon: " << (fScintEnergy / eV) / fScintCount << " eV."
             << G4endl;
    }
  }

  G4cout << "Average number of photons absorbed by WLS per event: "
         << fWLSAbsorptionCount / G4double(TotNbofEvents) << " " << G4endl;
  if (fWLSAbsorptionCount > 0) {
    G4cout << " Average energy per photon: " << (fWLSAbsorptionEnergy / eV) / fWLSAbsorptionCount
           << " eV." << G4endl;
  }
  G4cout << "Average number of photons created by WLS per event: "
         << fWLSEmissionCount / TotNbofEvents << G4endl;
  if (fWLSEmissionCount > 0) {
    G4cout << " Average energy per photon: " << (fWLSEmissionEnergy / eV) / fWLSEmissionCount
           << " eV." << G4endl;
  }
  G4cout << "Average energy of WLS photons created per event: "
         << (fWLSEmissionEnergy / eV) / TotNbofEvents << " eV." << G4endl;

  G4cout << "Average number of photons absorbed by WLS2 per event: "
         << fWLS2AbsorptionCount / G4double(TotNbofEvents) << " " << G4endl;
  if (fWLS2AbsorptionCount > 0) {
    G4cout << " Average energy per photon: " << (fWLS2AbsorptionEnergy / eV) / fWLS2AbsorptionCount
           << " eV." << G4endl;
  }
  G4cout << "Average number of photons created by WLS2 per event: "
         << fWLS2EmissionCount / TotNbofEvents << G4endl;
  if (fWLS2EmissionCount > 0) {
    G4cout << " Average energy per photon: " << (fWLS2EmissionEnergy / eV) / fWLS2EmissionCount
           << " eV." << G4endl;
  }
  G4cout << "Average energy of WLS2 photons created per event: "
         << (fWLS2EmissionEnergy / eV) / TotNbofEvents << " eV." << G4endl;

  G4cout << "Average number of OpRayleigh per event:   " << fRayleighCount / TotNbofEvents
         << G4endl;
  G4cout << "Average number of OpAbsorption per event: " << fOpAbsorption / TotNbofEvents << G4endl;

  // SiPM summary
  G4cout << "Average number of photons detected by SiPM per event: "
       << fSiPMDetectionCount / TotNbofEvents << G4endl;


  G4cout << "\nSurface events (on +X surface, maximum one per photon) this run:" << G4endl;
  G4cout << "# of primary particles:      " << std::setw(8) << TotNbofEvents << G4endl;
  G4cout << "OpAbsorption before surface: " << std::setw(8) << fOpAbsorptionPrior << G4endl;
  G4cout << "Total # of surface events:   " << std::setw(8) << fTotalSurface << G4endl;
  if (fParticle->GetParticleName() == "opticalphoton") {
    G4cout << "Unaccounted for:             " << std::setw(8)
           << fTotalSurface + fOpAbsorptionPrior - TotNbofEvents << G4endl;
  }
  G4cout << "\nSurface events by process:" << G4endl;
  if (fBoundaryProcs[Transmission] > 0) {
    G4cout << "  Transmission:              " << std::setw(8) << fBoundaryProcs[Transmission]
           << G4endl;
  }
  if (fBoundaryProcs[FresnelRefraction] > 0) {
    G4cout << "  Fresnel refraction:        " << std::setw(8) << fBoundaryProcs[FresnelRefraction]
           << G4endl;
  }
  if (fBoundaryProcs[FresnelReflection] > 0) {
    G4cout << "  Fresnel reflection:        " << std::setw(8) << fBoundaryProcs[FresnelReflection]
           << G4endl;
  }
  if (fBoundaryProcs[TotalInternalReflection] > 0) {
    G4cout << "  Total internal reflection: " << std::setw(8)
           << fBoundaryProcs[TotalInternalReflection] << G4endl;
  }
  if (fBoundaryProcs[LambertianReflection] > 0) {
    G4cout << "  Lambertian reflection:     " << std::setw(8)
           << fBoundaryProcs[LambertianReflection] << G4endl;
  }
  if (fBoundaryProcs[LobeReflection] > 0) {
    G4cout << "  Lobe reflection:           " << std::setw(8) << fBoundaryProcs[LobeReflection]
           << G4endl;
  }
  if (fBoundaryProcs[SpikeReflection] > 0) {
    G4cout << "  Spike reflection:          " << std::setw(8) << fBoundaryProcs[SpikeReflection]
           << G4endl;
  }
  if (fBoundaryProcs[BackScattering] > 0) {
    G4cout << "  Backscattering:            " << std::setw(8) << fBoundaryProcs[BackScattering]
           << G4endl;
  }
  if (fBoundaryProcs[Absorption] > 0) {
    G4cout << "  Absorption:                " << std::setw(8) << fBoundaryProcs[Absorption]
           << G4endl;
  }
  if (fBoundaryProcs[Detection] > 0) {
    G4cout << "  Detection:                 " << std::setw(8) << fBoundaryProcs[Detection]
           << G4endl;
  }
  if (fBoundaryProcs[NotAtBoundary] > 0) {
    G4cout << "  Not at boundary:           " << std::setw(8) << fBoundaryProcs[NotAtBoundary]
           << G4endl;
  }
  if (fBoundaryProcs[SameMaterial] > 0) {
    G4cout << "  Same material:             " << std::setw(8) << fBoundaryProcs[SameMaterial]
           << G4endl;
  }
  if (fBoundaryProcs[StepTooSmall] > 0) {
    G4cout << "  Step too small:            " << std::setw(8) << fBoundaryProcs[StepTooSmall]
           << G4endl;
  }
  if (fBoundaryProcs[NoRINDEX] > 0) {
    G4cout << "  No RINDEX:                 " << std::setw(8) << fBoundaryProcs[NoRINDEX] << G4endl;
  }
  // LBNL polished
  if (fBoundaryProcs[PolishedLumirrorAirReflection] > 0) {
    G4cout << "  Polished Lumirror Air reflection: " << std::setw(8)
           << fBoundaryProcs[PolishedLumirrorAirReflection] << G4endl;
  }
  if (fBoundaryProcs[PolishedLumirrorGlueReflection] > 0) {
    G4cout << "  Polished Lumirror Glue reflection: " << std::setw(8)
           << fBoundaryProcs[PolishedLumirrorGlueReflection] << G4endl;
  }
  if (fBoundaryProcs[PolishedAirReflection] > 0) {
    G4cout << "  Polished Air reflection: " << std::setw(8) << fBoundaryProcs[PolishedAirReflection]
           << G4endl;
  }
  if (fBoundaryProcs[PolishedTeflonAirReflection] > 0) {
    G4cout << "  Polished Teflon Air reflection: " << std::setw(8)
           << fBoundaryProcs[PolishedTeflonAirReflection] << G4endl;
  }
  if (fBoundaryProcs[PolishedTiOAirReflection] > 0) {
    G4cout << "  Polished TiO Air reflection: " << std::setw(8)
           << fBoundaryProcs[PolishedTiOAirReflection] << G4endl;
  }
  if (fBoundaryProcs[PolishedTyvekAirReflection] > 0) {
    G4cout << "  Polished Tyvek Air reflection: " << std::setw(8)
           << fBoundaryProcs[PolishedTyvekAirReflection] << G4endl;
  }
  if (fBoundaryProcs[PolishedVM2000AirReflection] > 0) {
    G4cout << "  Polished VM2000 Air reflection: " << std::setw(8)
           << fBoundaryProcs[PolishedVM2000AirReflection] << G4endl;
  }
  if (fBoundaryProcs[PolishedVM2000GlueReflection] > 0) {
    G4cout << "  Polished VM2000 Glue reflection: " << std::setw(8)
           << fBoundaryProcs[PolishedVM2000GlueReflection] << G4endl;
  }
  // LBNL etched
  if (fBoundaryProcs[EtchedLumirrorAirReflection] > 0) {
    G4cout << "  Etched Lumirror Air reflection: " << std::setw(8)
           << fBoundaryProcs[EtchedLumirrorAirReflection] << G4endl;
  }
  if (fBoundaryProcs[EtchedLumirrorGlueReflection] > 0) {
    G4cout << "  Etched Lumirror Glue reflection: " << std::setw(8)
           << fBoundaryProcs[EtchedLumirrorGlueReflection] << G4endl;
  }
  if (fBoundaryProcs[EtchedAirReflection] > 0) {
    G4cout << "  Etched Air reflection: " << std::setw(8) << fBoundaryProcs[EtchedAirReflection]
           << G4endl;
  }
  if (fBoundaryProcs[EtchedTeflonAirReflection] > 0) {
    G4cout << "  Etched Teflon Air reflection: " << std::setw(8)
           << fBoundaryProcs[EtchedTeflonAirReflection] << G4endl;
  }
  if (fBoundaryProcs[EtchedTiOAirReflection] > 0) {
    G4cout << "  Etched TiO Air reflection: " << std::setw(8)
           << fBoundaryProcs[EtchedTiOAirReflection] << G4endl;
  }
  if (fBoundaryProcs[EtchedTyvekAirReflection] > 0) {
    G4cout << "  Etched Tyvek Air reflection: " << std::setw(8)
           << fBoundaryProcs[EtchedTyvekAirReflection] << G4endl;
  }
  if (fBoundaryProcs[EtchedVM2000AirReflection] > 0) {
    G4cout << "  Etched VM2000 Air reflection: " << std::setw(8)
           << fBoundaryProcs[EtchedVM2000AirReflection] << G4endl;
  }
  if (fBoundaryProcs[EtchedVM2000GlueReflection] > 0) {
    G4cout << "  Etched VM2000 Glue reflection: " << std::setw(8)
           << fBoundaryProcs[EtchedVM2000GlueReflection] << G4endl;
  }
  // LBNL ground
  if (fBoundaryProcs[GroundLumirrorAirReflection] > 0) {
    G4cout << "  Ground Lumirror Air reflection: " << std::setw(8)
           << fBoundaryProcs[GroundLumirrorAirReflection] << G4endl;
  }
  if (fBoundaryProcs[GroundLumirrorGlueReflection] > 0) {
    G4cout << "  Ground Lumirror Glue reflection: " << std::setw(8)
           << fBoundaryProcs[GroundLumirrorGlueReflection] << G4endl;
  }
  if (fBoundaryProcs[GroundAirReflection] > 0) {
    G4cout << "  Ground Air reflection: " << std::setw(8) << fBoundaryProcs[GroundAirReflection]
           << G4endl;
  }
  if (fBoundaryProcs[GroundTeflonAirReflection] > 0) {
    G4cout << "  Ground Teflon Air reflection: " << std::setw(8)
           << fBoundaryProcs[GroundTeflonAirReflection] << G4endl;
  }
  if (fBoundaryProcs[GroundTiOAirReflection] > 0) {
    G4cout << "  Ground TiO Air reflection: " << std::setw(8)
           << fBoundaryProcs[GroundTiOAirReflection] << G4endl;
  }
  if (fBoundaryProcs[GroundTyvekAirReflection] > 0) {
    G4cout << "  Ground Tyvek Air reflection: " << std::setw(8)
           << fBoundaryProcs[GroundTyvekAirReflection] << G4endl;
  }
  if (fBoundaryProcs[GroundVM2000AirReflection] > 0) {
    G4cout << "  Ground VM2000 Air reflection: " << std::setw(8)
           << fBoundaryProcs[GroundVM2000AirReflection] << G4endl;
  }
  if (fBoundaryProcs[GroundVM2000GlueReflection] > 0) {
    G4cout << "  Ground VM2000 Glue reflection: " << std::setw(8)
           << fBoundaryProcs[GroundVM2000GlueReflection] << G4endl;
  }
  if (fBoundaryProcs[CoatedDielectricRefraction] > 0) {
    G4cout << "  CoatedDielectricRefraction: " << std::setw(8)
           << fBoundaryProcs[CoatedDielectricRefraction] << G4endl;
  }
  if (fBoundaryProcs[CoatedDielectricReflection] > 0) {
    G4cout << "  CoatedDielectricReflection: " << std::setw(8)
           << fBoundaryProcs[CoatedDielectricReflection] << G4endl;
  }
  if (fBoundaryProcs[CoatedDielectricFrustratedTransmission] > 0) {
    G4cout << "  CoatedDielectricFrustratedTransmission: " << std::setw(8)
           << fBoundaryProcs[CoatedDielectricFrustratedTransmission] << G4endl;
  }

  // SiPM count vs. scint count
  if (fScintCount > 0) {
    G4cout << "SiPM collection fraction relative to created scintillation photons: "
          << G4double(fSiPMDetectionCount) / G4double(fScintCount) << G4endl;
  }

  G4int sum = std::accumulate(fBoundaryProcs.begin(), fBoundaryProcs.end(), 0);
  G4cout << " Sum:                        " << std::setw(8) << sum << G4endl;
  G4cout << " Unaccounted for:            " << std::setw(8) << fTotalSurface - sum << G4endl;

  G4cout << "---------------------------------\n";
  G4cout.setf(mode, std::ios::floatfield);
  G4cout.precision(prec);

  G4int histo_id_refract = analysisMan->GetH1Id("Fresnel refraction");
  G4int histo_id_reflect = analysisMan->GetH1Id("Fresnel reflection plus TIR");
  G4int histo_id_spike = analysisMan->GetH1Id("Spike reflection");
  G4int histo_id_absorption = analysisMan->GetH1Id("Absorption");

  if (analysisMan->GetH1Activation(histo_id_refract)
      && analysisMan->GetH1Activation(histo_id_reflect))
  {
    G4double rindex1 =
      det->GetTankMaterial()->GetMaterialPropertiesTable()->GetProperty(kRINDEX)->Value(fEkin);
    G4double rindex2 =
      det->GetWorldMaterial()->GetMaterialPropertiesTable()->GetProperty(kRINDEX)->Value(fEkin);

    auto histo_refract = analysisMan->GetH1(histo_id_refract);
    auto histo_reflect = analysisMan->GetH1(histo_id_reflect);
    // std::vector<G4double> refract;
    std::vector<G4double> reflect;
    // std::vector<G4double> tir;
    std::vector<G4double> tot;
    for (size_t i = 0; i < histo_refract->axis().bins(); ++i) {
      // refract.push_back(histo_refract->bin_height(i));
      reflect.push_back(histo_reflect->bin_height(i));
      // tir.push_back(histo_TIR->bin_height(i));
      tot.push_back(histo_refract->bin_height(i) + histo_reflect->bin_height(i));
    }

    // find Brewster angle: Rp = 0
    //  need enough statistics for this method to work
    G4double min_angle = -1.;
    G4double min_val = DBL_MAX;
    G4double bin_width = 0.;
    for (size_t i = 0; i < reflect.size(); ++i) {
      if (reflect[i] < min_val) {
        min_val = reflect[i];
        min_angle = histo_reflect->axis().bin_lower_edge(i);
        bin_width =
          histo_reflect->axis().bin_upper_edge(i) - histo_reflect->axis().bin_lower_edge(i);
        min_angle += bin_width / 2.;
      }
    }
    G4cout << "Polarization of primary optical photons: " << fPolarization / deg << " deg."
           << G4endl;
    if (fPolarized && fPolarization == 0.0) {
      G4cout << "Reflectance shows a minimum at: " << min_angle << " +/- " << bin_width / 2;
      G4cout << " deg. Expected Brewster angle: "
             << (360. / CLHEP::twopi) * std::atan(rindex2 / rindex1) << " deg. " << G4endl;
    }

    // find angle of total internal reflection:  T -> 0
    //   last bin for T > 0
    min_angle = -1.;
    min_val = DBL_MAX;
    for (size_t i = 0; i < histo_refract->axis().bins() - 1; ++i) {
      if (histo_refract->bin_height(i) > 0. && histo_refract->bin_height(i + 1) == 0.) {
        min_angle = histo_refract->axis().bin_lower_edge(i);
        bin_width =
          histo_reflect->axis().bin_upper_edge(i) - histo_reflect->axis().bin_lower_edge(i);
        min_angle += bin_width / 2.;
        break;
      }
    }
    if (fPolarized) {
      G4cout << "Fresnel transmission goes to 0 at: " << min_angle << " +/- " << bin_width / 2.
             << " deg."
             << " Expected: " << (360. / CLHEP::twopi) * std::asin(rindex2 / rindex1) << " deg."
             << G4endl;
    }

    // Normalize the transmission/reflection histos so that max is 1.
    // Only if x values are the same
    if ((analysisMan->GetH1Nbins(histo_id_refract) == analysisMan->GetH1Nbins(histo_id_reflect))
        && (analysisMan->GetH1Xmin(histo_id_refract) == analysisMan->GetH1Xmin(histo_id_reflect))
        && (analysisMan->GetH1Xmax(histo_id_refract) == analysisMan->GetH1Xmax(histo_id_reflect)))
    {
      unsigned int ent;
      G4double sw;
      G4double sw2;
      G4double sx2;
      G4double sx2w;
      for (size_t bin = 0; bin < histo_refract->axis().bins(); ++bin) {
        // "bin+1" below because bin 0 is underflow bin
        // NB. We are ignoring underflow/overflow bins
        histo_refract->get_bin_content(bin + 1, ent, sw, sw2, sx2, sx2w);
        if (tot[bin] > 0) {
          sw /= tot[bin];
          // bin error is sqrt(sw2)
          sw2 /= (tot[bin] * tot[bin]);
          sx2 /= (tot[bin] * tot[bin]);
          sx2w /= (tot[bin] * tot[bin]);
          histo_refract->set_bin_content(bin + 1, ent, sw, sw2, sx2, sx2w);
        }

        histo_reflect->get_bin_content(bin + 1, ent, sw, sw2, sx2, sx2w);
        if (tot[bin] > 0) {
          sw /= tot[bin];
          // bin error is sqrt(sw2)
          sw2 /= (tot[bin] * tot[bin]);
          sx2 /= (tot[bin] * tot[bin]);
          sx2w /= (tot[bin] * tot[bin]);
          histo_reflect->set_bin_content(bin + 1, ent, sw, sw2, sx2, sx2w);
        }

        G4int histo_id_fresnelrefl = analysisMan->GetH1Id("Fresnel reflection");
        auto histo_fresnelreflect = analysisMan->GetH1(histo_id_fresnelrefl);
        histo_fresnelreflect->get_bin_content(bin + 1, ent, sw, sw2, sx2, sx2w);
        if (tot[bin] > 0) {
          sw /= tot[bin];
          // bin error is sqrt(sw2)
          sw2 /= (tot[bin] * tot[bin]);
          sx2 /= (tot[bin] * tot[bin]);
          sx2w /= (tot[bin] * tot[bin]);
          histo_fresnelreflect->set_bin_content(bin + 1, ent, sw, sw2, sx2, sx2w);
        }

        G4int histo_id_TIR = analysisMan->GetH1Id("Total internal reflection");
        auto histo_TIR = analysisMan->GetH1(histo_id_TIR);
        if (analysisMan->GetH1Activation(histo_id_TIR)) {
          histo_TIR->get_bin_content(bin + 1, ent, sw, sw2, sx2, sx2w);
          if (tot[bin] > 0) {
            sw /= tot[bin];
            // bin error is sqrt(sw2)
            sw2 /= (tot[bin] * tot[bin]);
            sx2 /= (tot[bin] * tot[bin]);
            sx2w /= (tot[bin] * tot[bin]);
            histo_TIR->set_bin_content(bin + 1, ent, sw, sw2, sx2, sx2w);
          }
        }
      }
    }
    else {
      G4cout << "Not going to normalize refraction and reflection "
             << "histograms because bins are not the same." << G4endl;
    }
  }

  // complex index of refraction; have spike reflection and absorption
  // Only works for polished surfaces. Ground surfaces neglected.
  else if (analysisMan->GetH1Activation(histo_id_absorption)
           && analysisMan->GetH1Activation(histo_id_spike))
  {
    auto histo_spike = analysisMan->GetH1(histo_id_spike);
    auto histo_absorption = analysisMan->GetH1(histo_id_absorption);

    std::vector<G4double> tot;
    for (size_t i = 0; i < histo_absorption->axis().bins(); ++i) {
      tot.push_back(histo_absorption->bin_height(i) + histo_spike->bin_height(i));
    }

    if ((analysisMan->GetH1Nbins(histo_id_absorption) == analysisMan->GetH1Nbins(histo_id_spike))
        && (analysisMan->GetH1Xmin(histo_id_absorption) == analysisMan->GetH1Xmin(histo_id_spike))
        && (analysisMan->GetH1Xmax(histo_id_absorption) == analysisMan->GetH1Xmax(histo_id_spike)))
    {
      unsigned int ent;
      G4double sw;
      G4double sw2;
      G4double sx2;
      G4double sx2w;
      for (size_t bin = 0; bin < histo_absorption->axis().bins(); ++bin) {
        // "bin+1" below because bin 0 is underflow bin
        // NB. We are ignoring underflow/overflow bins
        histo_absorption->get_bin_content(bin + 1, ent, sw, sw2, sx2, sx2w);
        if (tot[bin] > 0) {
          sw /= tot[bin];
          // bin error is sqrt(sw2)
          sw2 /= (tot[bin] * tot[bin]);
          sx2 /= (tot[bin] * tot[bin]);
          sx2w /= (tot[bin] * tot[bin]);
          histo_absorption->set_bin_content(bin + 1, ent, sw, sw2, sx2, sx2w);
        }

        histo_spike->get_bin_content(bin + 1, ent, sw, sw2, sx2, sx2w);
        if (tot[bin] > 0) {
          sw /= tot[bin];
          // bin error is sqrt(sw2)
          sw2 /= (tot[bin] * tot[bin]);
          sx2 /= (tot[bin] * tot[bin]);
          sx2w /= (tot[bin] * tot[bin]);
          histo_spike->set_bin_content(bin + 1, ent, sw, sw2, sx2, sx2w);
        }
      }
    }
    else {
      G4cout << "Not going to normalize spike reflection and absorption "
             << "histograms because bins are not the same." << G4endl;
    }
  }
}
