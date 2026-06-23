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
/// \file optical/OpNovice2/src/DetectorConstruction.cc
/// \brief Implementation of the DetectorConstruction class
//
//
//
//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......

#include "DetectorConstruction.hh"

#include "DetectorMessenger.hh"

#include "G4Box.hh"
#include "G4Element.hh"
#include "G4LogicalBorderSurface.hh"
#include "G4LogicalSkinSurface.hh"
#include "G4LogicalVolume.hh"
#include "G4Material.hh"
#include "G4NistManager.hh"
#include "G4OpticalSurface.hh"
#include "G4PVPlacement.hh"
#include "G4Sphere.hh"
#include "G4SubtractionSolid.hh"
#include "G4SystemOfUnits.hh"
#include "G4ThreeVector.hh"
#include "G4VSolid.hh"

#include "G4Colour.hh"
#include "G4VisAttributes.hh"

#include <algorithm>
#include <cmath>

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......

DetectorConstruction::DetectorConstruction()
  : G4VUserDetectorConstruction(), fDetectorMessenger(nullptr)
{
  fTankMPT = new G4MaterialPropertiesTable();
  fWorldMPT = new G4MaterialPropertiesTable();
  fSurfaceMPT = new G4MaterialPropertiesTable();
  // The properties table of SiPM
  fSiPMMPT = new G4MaterialPropertiesTable();

  fSurface = new G4OpticalSurface("Surface");
  fSurface->SetType(dielectric_dielectric);
  fSurface->SetFinish(ground);
  fSurface->SetModel(unified);
  fSurface->SetMaterialPropertiesTable(fSurfaceMPT);

  fTankMaterial = G4NistManager::Instance()->FindOrBuildMaterial("G4_PLASTIC_SC_VINYLTOLUENE");
  fWorldMaterial = G4NistManager::Instance()->FindOrBuildMaterial("G4_AIR");
  // The material of SiPM
  fSiPMMaterial = G4NistManager::Instance()->FindOrBuildMaterial("G4_Si");

  fDetectorMessenger = new DetectorMessenger(this);

  // ----- SiPM -----
  const G4int nEntries = 2;
  G4double photonEnergy[nEntries] = {2.0 * eV, 3.3 * eV};

  // Toy SiPM/silicon optical properties.
  // This is enough for first-pass photon entry + detection.
  G4double siRIndex[nEntries] = {4.0, 4.0};

  // Strong absorption once photons enter SiPM.
  G4double siAbsLength[nEntries] = {1.0 * um, 1.0 * um};

  fSiPMMPT->AddProperty("RINDEX", photonEnergy, siRIndex, nEntries);
  fSiPMMPT->AddProperty("ABSLENGTH", photonEnergy, siAbsLength, nEntries);
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......

DetectorConstruction::~DetectorConstruction()
{
  delete fTankMPT;
  delete fWorldMPT;
  delete fSurfaceMPT;
  // Frees the memory of SiPM
  delete fSiPMMPT;
  delete fSurface;
  delete fDetectorMessenger;
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......

G4VPhysicalVolume* DetectorConstruction::Construct()
{
  fTankMaterial->SetMaterialPropertiesTable(fTankMPT);
  fTankMaterial->GetIonisation()->SetBirksConstant(0.126 * mm / MeV);

  fWorldMaterial->SetMaterialPropertiesTable(fWorldMPT);

  // SiPM Properties Table
  fSiPMMaterial->SetMaterialPropertiesTable(fSiPMMPT);

  // ------------- Volumes --------------
  // The experimental Hall
  auto world_box = new G4Box("World", fExpHall_x, fExpHall_y, fExpHall_z);

  fWorld_LV = new G4LogicalVolume(world_box, fWorldMaterial, "World");

  G4VPhysicalVolume* world_PV =
    new G4PVPlacement(nullptr, G4ThreeVector(), fWorld_LV, "World", nullptr, false, 0);

  // The tank
  auto tank_box = new G4Box("Tank_Box", fTank_x, fTank_y, fTank_z);
  G4VSolid* tank_solid = tank_box;

  if (fBottomCavityEnabled && fDimpleEnabled) {
    G4ExceptionDescription msg;
    msg << "/opnovice2/dimple/enabled cannot be combined with "
        << "/opnovice2/tank/bottomCavity true. Use one cavity model at a time.";
    G4Exception("DetectorConstruction::Construct",
                "OpNovice2_Dimple_001",
                FatalException,
                msg);
  }

  if (fBottomCavityEnabled) {
    auto bottomCavitySphere = new G4Sphere("Tank_BottomCavitySphere",
                                           0.,
                                           GetBottomCavityRadius(),
                                           0.,
                                           360. * deg,
                                           0.,
                                           180. * deg);

    tank_solid = new G4SubtractionSolid("Tank",
                                        tank_box,
                                        bottomCavitySphere,
                                        nullptr,
                                        G4ThreeVector(0., 0., -fTank_z));
  }
  else if (fDimpleEnabled) {
    ValidateDimpleConfiguration();

    auto dimpleSphere = new G4Sphere("Tank_DimpleSphere",
                                     0.,
                                     fDimpleRadius,
                                     0.,
                                     360. * deg,
                                     0.,
                                     180. * deg);

    tank_solid = new G4SubtractionSolid("Tank",
                                        tank_box,
                                        dimpleSphere,
                                        nullptr,
                                        G4ThreeVector(0., 0., -fTank_z));
  }

  fTank_LV = new G4LogicalVolume(tank_solid, fTankMaterial, "Tank");

  fTank = new G4PVPlacement(nullptr, G4ThreeVector(), fTank_LV, "Tank", fWorld_LV, false, 0);

  // The SiPM
  G4double sipmHx = 0.0;
  G4double sipmHy = 0.0;
  G4double sipmHz = 0.0;
  G4ThreeVector sipmPos;

  ComputeSiPMPlacement(sipmHx, sipmHy, sipmHz, sipmPos);

  auto sipm_box = new G4Box("SiPM_Box", sipmHx, sipmHy, sipmHz);

  fSiPM_LV = new G4LogicalVolume(sipm_box, fSiPMMaterial, "SiPM");

  // Visual attributes of SiPM
  auto sipmVis = new G4VisAttributes(G4Colour(0.0, 1.0, 0.0, 0.9));
  sipmVis->SetForceSolid(true);
  fSiPM_LV->SetVisAttributes(sipmVis);

  fSiPM = new G4PVPlacement(nullptr,
                            sipmPos,
                            fSiPM_LV,
                            "SiPM",
                            fWorld_LV,
                            false,
                            0,
                            true);        // overlap check


  // ------------- Surface --------------

  auto surface = new G4LogicalBorderSurface("Surface", fTank, world_PV, fSurface);

  auto opticalSurface =
    dynamic_cast<G4OpticalSurface*>(surface->GetSurface(fTank, world_PV)->GetSurfaceProperty());
  G4cout << "******  opticalSurface->DumpInfo:" << G4endl;
  if (opticalSurface) {
    opticalSurface->DumpInfo();
  }
  G4cout << "******  end of opticalSurface->DumpInfo" << G4endl;

  return world_PV;
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
void DetectorConstruction::SetSurfaceSigmaAlpha(G4double v)
{
  fSurface->SetSigmaAlpha(v);
  G4RunManager::GetRunManager()->GeometryHasBeenModified();

  G4cout << "Surface sigma alpha set to: " << fSurface->GetSigmaAlpha() << G4endl;
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
void DetectorConstruction::SetSurfacePolish(G4double v)
{
  fSurface->SetPolish(v);
  G4RunManager::GetRunManager()->GeometryHasBeenModified();

  G4cout << "Surface polish set to: " << fSurface->GetPolish() << G4endl;
}

void DetectorConstruction::ResetSurfaceMaterialPropertiesTable()
{
  delete fSurfaceMPT;
  fSurfaceMPT = new G4MaterialPropertiesTable();
  fSurface->SetMaterialPropertiesTable(fSurfaceMPT);
}

void DetectorConstruction::SetSurfacePreset(const G4String& preset)
{
  ResetSurfaceMaterialPropertiesTable();
  fSurface->SetModel(unified);
  fSurface->SetType(dielectric_dielectric);

  if (preset == "polished") {
    fSurface->SetFinish(polished);
    fSurface->SetSigmaAlpha(0.0);
  }
  else if (preset == "ground") {
    fSurface->SetFinish(ground);
    fSurface->SetSigmaAlpha(0.2);
  }
  else if (preset == "wrapped") {
    fSurface->SetFinish(polishedfrontpainted);
    fSurface->SetSigmaAlpha(0.0);

    const G4int nEntries = 2;
    G4double photonEnergy[nEntries] = {2.0 * eV, 3.3 * eV};
    G4double reflectivity[nEntries] = {0.95, 0.95};
    fSurfaceMPT->AddProperty("REFLECTIVITY", photonEnergy, reflectivity, nEntries);
  }
  else {
    G4ExceptionDescription msg;
    msg << "Invalid surface preset: " << preset
        << ". Use polished, ground, or wrapped.";
    G4Exception("DetectorConstruction::SetSurfacePreset",
                "OpNovice2_Surface_001",
                FatalException,
                msg);
  }

  G4RunManager::GetRunManager()->GeometryHasBeenModified();
  G4cout << "Surface preset applied: " << preset
         << " (model=unified, type=dielectric_dielectric, finish="
         << fSurface->GetFinish() << ", sigma_alpha="
         << fSurface->GetSigmaAlpha() << ")" << G4endl;
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
void DetectorConstruction::AddTankMPV(const G4String& prop, G4MaterialPropertyVector* mpv)
{
  fTankMPT->AddProperty(prop, mpv);
  G4cout << "The MPT for the box is now: " << G4endl;
  fTankMPT->DumpTable();
  G4cout << "............." << G4endl;
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
void DetectorConstruction::AddWorldMPV(const G4String& prop, G4MaterialPropertyVector* mpv)
{
  fWorldMPT->AddProperty(prop, mpv);
  G4cout << "The MPT for the world is now: " << G4endl;
  fWorldMPT->DumpTable();
  G4cout << "............." << G4endl;
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
void DetectorConstruction::AddSurfaceMPV(const G4String& prop, G4MaterialPropertyVector* mpv)
{
  if (fSurfaceMPT->GetProperty(prop) != nullptr) {
    fSurfaceMPT->RemoveProperty(prop);
  }
  fSurfaceMPT->AddProperty(prop, mpv);
  G4cout << "The MPT for the surface is now: " << G4endl;
  fSurfaceMPT->DumpTable();
  G4cout << "............." << G4endl;
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
void DetectorConstruction::AddTankMPC(const G4String& prop, G4double v)
{
  fTankMPT->AddConstProperty(prop, v);
  G4cout << "The MPT for the box is now: " << G4endl;
  fTankMPT->DumpTable();
  G4cout << "............." << G4endl;
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
void DetectorConstruction::AddWorldMPC(const G4String& prop, G4double v)
{
  fWorldMPT->AddConstProperty(prop, v);
  G4cout << "The MPT for the world is now: " << G4endl;
  fWorldMPT->DumpTable();
  G4cout << "............." << G4endl;
}
//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
void DetectorConstruction::AddSurfaceMPC(const G4String& prop, G4double v)
{
  if (fSurfaceMPT->ConstPropertyExists(prop)) {
    fSurfaceMPT->RemoveConstProperty(prop);
  }
  fSurfaceMPT->AddConstProperty(prop, v);
  G4cout << "The MPT for the surface is now: " << G4endl;
  fSurfaceMPT->DumpTable();
  G4cout << "............." << G4endl;
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
void DetectorConstruction::SetWorldMaterial(const G4String& mat)
{
  G4Material* pmat = G4NistManager::Instance()->FindOrBuildMaterial(mat);
  if (pmat && fWorldMaterial != pmat) {
    fWorldMaterial = pmat;
    if (fWorld_LV) {
      fWorld_LV->SetMaterial(fWorldMaterial);
      fWorldMaterial->SetMaterialPropertiesTable(fWorldMPT);
    }
    G4RunManager::GetRunManager()->PhysicsHasBeenModified();
    G4cout << "World material set to " << fWorldMaterial->GetName() << G4endl;
  }
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
void DetectorConstruction::SetTankMaterial(const G4String& mat)
{
  G4Material* pmat = G4NistManager::Instance()->FindOrBuildMaterial(mat);
  if (pmat && fTankMaterial != pmat) {
    fTankMaterial = pmat;
    if (fTank_LV) {
      fTank_LV->SetMaterial(fTankMaterial);
      fTankMaterial->SetMaterialPropertiesTable(fTankMPT);
      fTankMaterial->GetIonisation()->SetBirksConstant(0.126 * mm / MeV);
    }
    G4RunManager::GetRunManager()->PhysicsHasBeenModified();
    G4cout << "Tank material set to " << fTankMaterial->GetName() << G4endl;
  }
}

void DetectorConstruction::SetTankSize(const G4ThreeVector& fullSize)
{
  if (fullSize.x() <= 0. || fullSize.y() <= 0. || fullSize.z() <= 0.) {
    G4ExceptionDescription msg;
    msg << "Invalid tank full size: " << fullSize / cm
        << " cm. All dimensions must be positive.";
    G4Exception("DetectorConstruction::SetTankSize",
                "OpNovice2_Tank_001",
                FatalException,
                msg);
  }

  fTank_x = 0.5 * fullSize.x();
  fTank_y = 0.5 * fullSize.y();
  fTank_z = 0.5 * fullSize.z();
  G4RunManager::GetRunManager()->GeometryHasBeenModified();

  G4cout << "Tank full size set to "
         << 2. * fTank_x / cm << " x "
         << 2. * fTank_y / cm << " x "
         << 2. * fTank_z / cm << " cm" << G4endl;
}

void DetectorConstruction::SetTankSizePreset(const G4String& preset)
{
  if (preset == "5x5x0p4") {
    SetTankSize(G4ThreeVector(5. * cm, 5. * cm, 0.4 * cm));
  }
  else if (preset == "5x5x0p8") {
    SetTankSize(G4ThreeVector(5. * cm, 5. * cm, 0.8 * cm));
  }
  else if (preset == "5x5x1p6") {
    SetTankSize(G4ThreeVector(5. * cm, 5. * cm, 1.6 * cm));
  }
  else if (preset == "10x10x0p4") {
    SetTankSize(G4ThreeVector(10. * cm, 10. * cm, 0.4 * cm));
  }
  else if (preset == "10x10x0p8") {
    SetTankSize(G4ThreeVector(10. * cm, 10. * cm, 0.8 * cm));
  }
  else if (preset == "10x10x1p6") {
    SetTankSize(G4ThreeVector(10. * cm, 10. * cm, 1.6 * cm));
  }
  else {
    G4ExceptionDescription msg;
    msg << "Unknown tank size preset: " << preset
        << ". Use 5x5x0p4, 5x5x0p8, 5x5x1p6, "
        << "10x10x0p4, 10x10x0p8, or 10x10x1p6.";
    G4Exception("DetectorConstruction::SetTankSizePreset",
                "OpNovice2_Tank_002",
                FatalException,
                msg);
  }
}

void DetectorConstruction::SetBottomCavityEnabled(G4bool enabled)
{
  fBottomCavityEnabled = enabled;
  G4RunManager::GetRunManager()->GeometryHasBeenModified();

  G4cout << "Tank bottom cavity set to "
         << (fBottomCavityEnabled ? "true" : "false") << G4endl;
}

void DetectorConstruction::SetDimpleEnabled(G4bool enabled)
{
  fDimpleEnabled = enabled;
  G4RunManager::GetRunManager()->GeometryHasBeenModified();

  G4cout << "Dimple set to "
         << (fDimpleEnabled ? "true" : "false") << G4endl;
}

void DetectorConstruction::SetDimpleRadius(G4double radius)
{
  if (radius <= 0.) {
    G4ExceptionDescription msg;
    msg << "Invalid dimple radius: " << radius / mm
        << " mm. Radius must be positive.";
    G4Exception("DetectorConstruction::SetDimpleRadius",
                "OpNovice2_Dimple_012",
                FatalException,
                msg);
  }

  fDimpleRadius = radius;
  G4RunManager::GetRunManager()->GeometryHasBeenModified();

  G4cout << "Dimple radius set to " << fDimpleRadius / mm << " mm" << G4endl;
}

void DetectorConstruction::SetDimpleMode(const G4String& mode)
{
  if (mode != "hemisphere") {
    G4ExceptionDescription msg;
    msg << "Invalid dimple mode: " << mode
        << ". Only hemisphere is supported in Week 8.1.";
    G4Exception("DetectorConstruction::SetDimpleMode",
                "OpNovice2_Dimple_013",
                FatalException,
                msg);
  }

  fDimpleMode = mode;
  G4RunManager::GetRunManager()->GeometryHasBeenModified();

  G4cout << "Dimple mode set to " << fDimpleMode << G4endl;
}

void DetectorConstruction::SetDimpleSiPMMode(const G4String& mode)
{
  if (mode != "surface" && mode != "opening") {
    G4ExceptionDescription msg;
    msg << "Invalid dimple SiPM mode: " << mode << ". Use surface or opening.";
    G4Exception("DetectorConstruction::SetDimpleSiPMMode",
                "OpNovice2_Dimple_014",
                FatalException,
                msg);
  }

  fDimpleSiPMMode = mode;
  fDimpleSiPMModeExplicit = true;
  G4RunManager::GetRunManager()->GeometryHasBeenModified();

  G4cout << "Dimple SiPM mode set to " << fDimpleSiPMMode << G4endl;
}

G4double DetectorConstruction::GetBottomCavityRadius() const
{
  const G4double tankThickness = 2. * fTank_z;
  const G4double minimumTopThickness = 0.25 * tankThickness;
  return tankThickness - minimumTopThickness;
}

G4String DetectorConstruction::GetEffectiveDimpleSiPMMode() const
{
  if (fDimpleSiPMModeExplicit && fSiPMCavityModeExplicit &&
      fDimpleSiPMMode != fSiPMCavityMode) {
    G4ExceptionDescription msg;
    msg << "Conflicting dimple SiPM placement modes: /opnovice2/dimple/sipmMode="
        << fDimpleSiPMMode << " but legacy /opnovice2/sipm/cavityMode="
        << fSiPMCavityMode << ". Use one mode, or set both to the same value.";
    G4Exception("DetectorConstruction::GetEffectiveDimpleSiPMMode",
                "OpNovice2_Dimple_002",
                FatalException,
                msg);
  }

  if (!fDimpleSiPMModeExplicit && fSiPMCavityModeExplicit) {
    return fSiPMCavityMode;
  }

  return fDimpleSiPMMode;
}

G4double DetectorConstruction::GetSiPMFootprintCornerRadius(G4double u,
                                                            G4double v,
                                                            G4double hu,
                                                            G4double hv) const
{
  const G4double cornerRadii[] = {
    std::sqrt((u - hu) * (u - hu) + (v - hv) * (v - hv)),
    std::sqrt((u - hu) * (u - hu) + (v + hv) * (v + hv)),
    std::sqrt((u + hu) * (u + hu) + (v - hv) * (v - hv)),
    std::sqrt((u + hu) * (u + hu) + (v + hv) * (v + hv))
  };

  G4double rMax = cornerRadii[0];
  for (G4int i = 1; i < 4; ++i) {
    rMax = std::max(rMax, cornerRadii[i]);
  }
  return rMax;
}

void DetectorConstruction::ValidateDimpleConfiguration() const
{
  const G4double safety = 1.e-6 * mm;

  if (fDimpleMode != "hemisphere") {
    G4ExceptionDescription msg;
    msg << "Invalid dimple mode: " << fDimpleMode
        << ". Only hemisphere is supported in Week 8.1.";
    G4Exception("DetectorConstruction::ValidateDimpleConfiguration",
                "OpNovice2_Dimple_003",
                FatalException,
                msg);
  }

  if (fDimpleRadius <= 0.) {
    G4ExceptionDescription msg;
    msg << "Invalid dimple radius: " << fDimpleRadius / mm
        << " mm. Radius must be positive.";
    G4Exception("DetectorConstruction::ValidateDimpleConfiguration",
                "OpNovice2_Dimple_004",
                FatalException,
                msg);
  }

  const G4double tankThickness = 2. * fTank_z;
  if (fDimpleRadius >= tankThickness - safety) {
    G4ExceptionDescription msg;
    msg << "Strict hemispherical dimple radius is too large for this tile. "
        << "radius=" << fDimpleRadius / mm
        << " mm, tile thickness=" << tankThickness / mm
        << " mm. Radius must be smaller than thickness with safety margin.";
    G4Exception("DetectorConstruction::ValidateDimpleConfiguration",
                "OpNovice2_Dimple_005",
                FatalException,
                msg);
  }

  if (fSiPMFace != "-Z" && fSiPMFace != "bottom") {
    G4ExceptionDescription msg;
    msg << "Week 8.1 dimple supports only bottom-center -Z SiPM placement. "
        << "Current /opnovice2/sipm/face is " << fSiPMFace << ".";
    G4Exception("DetectorConstruction::ValidateDimpleConfiguration",
                "OpNovice2_Dimple_006",
                FatalException,
                msg);
  }

  if (std::abs(fSiPMLocalPosition.x()) > safety ||
      std::abs(fSiPMLocalPosition.y()) > safety ||
      std::abs(fSiPMLocalPosition.z()) > safety) {
    G4ExceptionDescription msg;
    msg << "Week 8.1 dimple supports only bottom-center SiPM local position "
        << "0 0 0. Current local position is "
        << fSiPMLocalPosition / mm << " mm.";
    G4Exception("DetectorConstruction::ValidateDimpleConfiguration",
                "OpNovice2_Dimple_007",
                FatalException,
                msg);
  }

  const G4double hu = 0.5 * fSiPMActiveU;
  const G4double hv = 0.5 * fSiPMActiveV;
  const G4double rMax = GetSiPMFootprintCornerRadius(0., 0., hu, hv);
  if (rMax >= fDimpleRadius - safety) {
    G4ExceptionDescription msg;
    msg << "SiPM active face does not fit inside the dimple opening. "
        << "farthest corner radius=" << rMax / mm
        << " mm, dimple radius=" << fDimpleRadius / mm << " mm.";
    G4Exception("DetectorConstruction::ValidateDimpleConfiguration",
                "OpNovice2_Dimple_008",
                FatalException,
                msg);
  }

  const G4String sipmMode = GetEffectiveDimpleSiPMMode();
  if (sipmMode != "surface" && sipmMode != "opening") {
    G4ExceptionDescription msg;
    msg << "Unknown dimple SiPM mode: " << sipmMode
        << ". Use surface or opening.";
    G4Exception("DetectorConstruction::ValidateDimpleConfiguration",
                "OpNovice2_Dimple_009",
                FatalException,
                msg);
  }

  if (sipmMode == "opening") {
    const G4double dimpleTopAtFootprint =
      -fTank_z + std::sqrt(fDimpleRadius * fDimpleRadius - rMax * rMax);
    const G4double topFace = -fTank_z + fSiPMThickness;
    if (topFace >= dimpleTopAtFootprint - safety) {
      G4ExceptionDescription msg;
      msg << "Dimple opening placement would overlap EJ-200. "
          << "top face z=" << topFace / mm
          << " mm, dimple clearance z=" << dimpleTopAtFootprint / mm << " mm.";
      G4Exception("DetectorConstruction::ValidateDimpleConfiguration",
                  "OpNovice2_Dimple_010",
                  FatalException,
                  msg);
    }
  }
}


void DetectorConstruction::ComputeSiPMPlacement(G4double& hx,
                                                G4double& hy,
                                                G4double& hz,
                                                G4ThreeVector& pos) const
{
  const G4double hu = 0.5 * fSiPMActiveU;
  const G4double hv = 0.5 * fSiPMActiveV;
  const G4double ht = 0.5 * fSiPMThickness;

  const G4double u = fSiPMLocalPosition.x();
  const G4double v = fSiPMLocalPosition.y();

  if (fSiPMFace == "+X" || fSiPMFace == "right") {
    hx = ht;
    hy = hu;
    hz = hv;
    pos = G4ThreeVector(fTank_x + ht, u, v);
  }
  else if (fSiPMFace == "-X" || fSiPMFace == "left") {
    hx = ht;
    hy = hu;
    hz = hv;
    pos = G4ThreeVector(-fTank_x - ht, u, v);
  }
  else if (fSiPMFace == "+Y" || fSiPMFace == "back") {
    hx = hu;
    hy = ht;
    hz = hv;
    pos = G4ThreeVector(u, fTank_y + ht, v);
  }
  else if (fSiPMFace == "-Y" || fSiPMFace == "front") {
    hx = hu;
    hy = ht;
    hz = hv;
    pos = G4ThreeVector(u, -fTank_y - ht, v);
  }
  else if (fSiPMFace == "+Z" || fSiPMFace == "top") {
    hx = hu;
    hy = hv;
    hz = ht;
    pos = G4ThreeVector(u, v, fTank_z + ht);
  }
  else if (fSiPMFace == "-Z" || fSiPMFace == "bottom") {
    hx = hu;
    hy = hv;
    hz = ht;
    if (fDimpleEnabled) {
      const G4double safety = 1.e-6 * mm;
      const G4double rMax = GetSiPMFootprintCornerRadius(u, v, hu, hv);
      const G4double dimpleTopAtFootprint =
        -fTank_z + std::sqrt(fDimpleRadius * fDimpleRadius - rMax * rMax);
      const G4String sipmMode = GetEffectiveDimpleSiPMMode();

      if (sipmMode == "surface") {
        pos = G4ThreeVector(u, v, dimpleTopAtFootprint - ht - safety);
      }
      else if (sipmMode == "opening") {
        pos = G4ThreeVector(u, v, -fTank_z + ht);
      }
      else {
        G4ExceptionDescription msg;
        msg << "Unknown dimple SiPM mode: " << sipmMode
            << ". Use surface or opening.";
        G4Exception("DetectorConstruction::ComputeSiPMPlacement",
                    "OpNovice2_Dimple_011",
                    FatalException,
                    msg);
      }
    }
    else {
      pos = G4ThreeVector(u, v, -fTank_z - ht);
    }
  }
  else if (fSiPMFace == "bottomCavity") {
    hx = hu;
    hy = hv;
    hz = ht;

    if (!fBottomCavityEnabled) {
      G4ExceptionDescription msg;
      msg << "SiPM face bottomCavity requires /opnovice2/tank/bottomCavity true.";
      G4Exception("DetectorConstruction::ComputeSiPMPlacement",
                  "OpNovice2_SiPM_002",
                  FatalException,
                  msg);
    }

    const G4double cavityRadius = GetBottomCavityRadius();
    const G4double rMax = GetSiPMFootprintCornerRadius(u, v, hu, hv);
    const G4double safety = 1.e-6 * mm;

    if (rMax >= cavityRadius - safety) {
      G4ExceptionDescription msg;
      msg << "SiPM footprint does not fit inside the bottom cavity opening. "
          << "farthest corner radius=" << rMax / mm
          << " mm, cavity radius=" << cavityRadius / mm << " mm.";
      G4Exception("DetectorConstruction::ComputeSiPMPlacement",
                  "OpNovice2_SiPM_003",
                  FatalException,
                  msg);
    }

    const G4double cavityTopAtFootprint =
      -fTank_z + std::sqrt(cavityRadius * cavityRadius - rMax * rMax);

    if (fSiPMCavityMode == "surface") {
      pos = G4ThreeVector(u, v, cavityTopAtFootprint - ht - safety);
    }
    else if (fSiPMCavityMode == "opening") {
      const G4double topFace = -fTank_z + fSiPMThickness;
      if (topFace >= cavityTopAtFootprint - safety) {
        G4ExceptionDescription msg;
        msg << "SiPM opening placement would overlap EJ-200. "
            << "top face z=" << topFace / mm
            << " mm, cavity clearance z=" << cavityTopAtFootprint / mm << " mm.";
        G4Exception("DetectorConstruction::ComputeSiPMPlacement",
                    "OpNovice2_SiPM_004",
                    FatalException,
                    msg);
      }
      pos = G4ThreeVector(u, v, -fTank_z + ht);
    }
    else {
      G4ExceptionDescription msg;
      msg << "Unknown SiPM cavity mode: " << fSiPMCavityMode
          << ". Use surface or opening.";
      G4Exception("DetectorConstruction::ComputeSiPMPlacement",
                  "OpNovice2_SiPM_005",
                  FatalException,
                  msg);
    }
  }
  else {
    G4ExceptionDescription msg;
    msg << "Unknown SiPM face: " << fSiPMFace
        << ". Use +X, -X, +Y, -Y, +Z, -Z, bottomCavity.";
    G4Exception("DetectorConstruction::ComputeSiPMPlacement",
                "OpNovice2_SiPM_001",
                FatalException,
                msg);
  }
}

void DetectorConstruction::SetSiPMFace(const G4String& face)
{
  fSiPMFace = face;
  G4RunManager::GetRunManager()->GeometryHasBeenModified();

  G4cout << "SiPM face set to " << fSiPMFace << G4endl;
}

void DetectorConstruction::SetSiPMCavityMode(const G4String& mode)
{
  if (mode != "surface" && mode != "opening") {
    G4ExceptionDescription msg;
    msg << "Invalid SiPM cavity mode: " << mode << ". Use surface or opening.";
    G4Exception("DetectorConstruction::SetSiPMCavityMode",
                "OpNovice2_SiPM_006",
                FatalException,
                msg);
  }

  fSiPMCavityMode = mode;
  fSiPMCavityModeExplicit = true;
  G4RunManager::GetRunManager()->GeometryHasBeenModified();

  G4cout << "SiPM cavity mode set to " << fSiPMCavityMode << G4endl;
}

void DetectorConstruction::SetSiPMLocalPosition(const G4ThreeVector& pos)
{
  fSiPMLocalPosition = pos;
  G4RunManager::GetRunManager()->GeometryHasBeenModified();

  G4cout << "SiPM local position set to "
         << fSiPMLocalPosition / cm << " cm" << G4endl;
}

void DetectorConstruction::SetSiPMSize(const G4ThreeVector& size)
{
  fSiPMActiveU = size.x();
  fSiPMActiveV = size.y();
  fSiPMThickness = size.z();

  G4RunManager::GetRunManager()->GeometryHasBeenModified();

  G4cout << "SiPM size set to activeU="
         << fSiPMActiveU / mm << " mm, activeV="
         << fSiPMActiveV / mm << " mm, thickness="
         << fSiPMThickness / mm << " mm" << G4endl;
}
