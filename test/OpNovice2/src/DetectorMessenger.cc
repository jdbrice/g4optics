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
/// \file optical/OpNovice2/src/DetectorMessenger.cc
/// \brief Implementation of the DetectorMessenger class
//
//
//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......

#include "DetectorMessenger.hh"

#include "DetectorConstruction.hh"

#include "G4OpticalSurface.hh"
#include "G4UIcmdWithADouble.hh"
#include "G4UIcmdWithADoubleAndUnit.hh"
#include "G4UIcmdWithABool.hh"
#include "G4UIcmdWithAString.hh"
#include "G4UIcmdWithAnInteger.hh"
#include "G4UIcmdWithoutParameter.hh"
#include "G4UIcommand.hh"
#include "G4UIdirectory.hh"
#include "G4UIparameter.hh"
#include "G4UIcmdWith3VectorAndUnit.hh"

#include <iostream>
#include <sstream>

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......

DetectorMessenger::DetectorMessenger(DetectorConstruction* Det) : G4UImessenger(), fDetector(Det)
{
  fOpticalDir = new G4UIdirectory("/opnovice2/");
  fOpticalDir->SetGuidance("Parameters for optical simulation.");

  fSurfaceTypeCmd = new G4UIcmdWithAString("/opnovice2/surfaceType", this);
  fSurfaceTypeCmd->SetGuidance("Surface type.");
  fSurfaceTypeCmd->AvailableForStates(G4State_PreInit, G4State_Idle);
  fSurfaceTypeCmd->SetToBeBroadcasted(false);

  fSurfaceFinishCmd = new G4UIcmdWithAString("/opnovice2/surfaceFinish", this);
  fSurfaceFinishCmd->SetGuidance("Surface finish.");
  fSurfaceFinishCmd->AvailableForStates(G4State_PreInit, G4State_Idle);
  fSurfaceFinishCmd->SetToBeBroadcasted(false);

  fSurfaceModelCmd = new G4UIcmdWithAString("/opnovice2/surfaceModel", this);
  fSurfaceModelCmd->SetGuidance("surface model.");
  fSurfaceModelCmd->AvailableForStates(G4State_PreInit, G4State_Idle);
  fSurfaceModelCmd->SetToBeBroadcasted(false);

  fSurfacePresetCmd = new G4UIcmdWithAString("/opnovice2/surfacePreset", this);
  fSurfacePresetCmd->SetGuidance("Apply a Week 7 surface preset.");
  fSurfacePresetCmd->SetGuidance("Options: polished, ground, wrapped.");
  fSurfacePresetCmd->AvailableForStates(G4State_PreInit, G4State_Idle);
  fSurfacePresetCmd->SetToBeBroadcasted(false);

  fSurfaceSigmaAlphaCmd = new G4UIcmdWithADouble("/opnovice2/surfaceSigmaAlpha", this);
  fSurfaceSigmaAlphaCmd->SetGuidance("surface sigma alpha");
  fSurfaceSigmaAlphaCmd->SetGuidance(" parameter.");
  fSurfaceSigmaAlphaCmd->AvailableForStates(G4State_PreInit, G4State_Idle);
  fSurfaceSigmaAlphaCmd->SetToBeBroadcasted(false);

  fSurfacePolishCmd = new G4UIcmdWithADouble("/opnovice2/surfacePolish", this);
  fSurfacePolishCmd->SetGuidance("surface polish");
  fSurfacePolishCmd->SetGuidance(" parameter (for Glisur model).");
  fSurfacePolishCmd->AvailableForStates(G4State_PreInit, G4State_Idle);
  fSurfacePolishCmd->SetToBeBroadcasted(false);

  fSurfaceMatPropVectorCmd = new G4UIcmdWithAString("/opnovice2/surfaceProperty", this);
  fSurfaceMatPropVectorCmd->SetGuidance("Set material property vector");
  fSurfaceMatPropVectorCmd->SetGuidance(" for the surface.");
  fSurfaceMatPropVectorCmd->AvailableForStates(G4State_PreInit, G4State_Idle);
  fSurfaceMatPropVectorCmd->SetToBeBroadcasted(false);

  fSurfaceMatPropConstCmd = new G4UIcmdWithAString("/opnovice2/surfaceConstProperty", this);
  fSurfaceMatPropConstCmd->SetGuidance("Set material constant property");
  fSurfaceMatPropConstCmd->SetGuidance(" for the surface.");
  fSurfaceMatPropConstCmd->AvailableForStates(G4State_PreInit, G4State_Idle);
  fSurfaceMatPropConstCmd->SetToBeBroadcasted(false);

  fTankMatPropVectorCmd = new G4UIcmdWithAString("/opnovice2/boxProperty", this);
  fTankMatPropVectorCmd->SetGuidance("Set material property vector for ");
  fTankMatPropVectorCmd->SetGuidance("the box.");
  fTankMatPropVectorCmd->AvailableForStates(G4State_PreInit, G4State_Idle);
  fTankMatPropVectorCmd->SetToBeBroadcasted(false);

  fTankMatPropConstCmd = new G4UIcmdWithAString("/opnovice2/boxConstProperty", this);
  fTankMatPropConstCmd->SetGuidance("Set material constant property ");
  fTankMatPropConstCmd->SetGuidance("for the box.");
  fTankMatPropConstCmd->AvailableForStates(G4State_PreInit, G4State_Idle);
  fTankMatPropConstCmd->SetToBeBroadcasted(false);

  fTankMaterialCmd = new G4UIcmdWithAString("/opnovice2/boxMaterial", this);
  fTankMaterialCmd->SetGuidance("Set material of box.");
  fTankMaterialCmd->AvailableForStates(G4State_PreInit, G4State_Idle);
  fTankMaterialCmd->SetToBeBroadcasted(false);

  fTankSizeCmd = new G4UIcmdWith3VectorAndUnit("/opnovice2/tank/size", this);
  fTankSizeCmd->SetGuidance("Set full EJ-200 tank size: x y thickness.");
  fTankSizeCmd->SetParameterName("x", "y", "thickness", false);
  fTankSizeCmd->SetUnitCategory("Length");
  fTankSizeCmd->SetDefaultUnit("cm");
  fTankSizeCmd->AvailableForStates(G4State_PreInit);
  fTankSizeCmd->SetToBeBroadcasted(false);

  fTankSizePresetCmd = new G4UIcmdWithAString("/opnovice2/tank/sizePreset", this);
  fTankSizePresetCmd->SetGuidance("Set EJ-200 tank size preset.");
  fTankSizePresetCmd->SetGuidance("Options: 5x5x0p4, 5x5x0p8, 5x5x1p6, 10x10x0p4, 10x10x0p8, 10x10x1p6.");
  fTankSizePresetCmd->AvailableForStates(G4State_PreInit);
  fTankSizePresetCmd->SetToBeBroadcasted(false);

  fTankBottomCavityCmd = new G4UIcmdWithABool("/opnovice2/tank/bottomCavity", this);
  fTankBottomCavityCmd->SetGuidance("Enable the bottom hemispherical tank cavity.");
  fTankBottomCavityCmd->SetDefaultValue(false);
  fTankBottomCavityCmd->AvailableForStates(G4State_PreInit);
  fTankBottomCavityCmd->SetToBeBroadcasted(false);

  fDimpleEnabledCmd = new G4UIcmdWithABool("/opnovice2/dimple/enabled", this);
  fDimpleEnabledCmd->SetGuidance("Enable the Week 8.1 bottom-center hemispherical dimple.");
  fDimpleEnabledCmd->SetDefaultValue(false);
  fDimpleEnabledCmd->AvailableForStates(G4State_PreInit);
  fDimpleEnabledCmd->SetToBeBroadcasted(false);

  fDimpleRadiusCmd = new G4UIcmdWithADoubleAndUnit("/opnovice2/dimple/radius", this);
  fDimpleRadiusCmd->SetGuidance("Set strict hemispherical dimple radius and depth.");
  fDimpleRadiusCmd->SetParameterName("radius", false);
  fDimpleRadiusCmd->SetUnitCategory("Length");
  fDimpleRadiusCmd->SetDefaultUnit("mm");
  fDimpleRadiusCmd->AvailableForStates(G4State_PreInit);
  fDimpleRadiusCmd->SetToBeBroadcasted(false);

  fDimpleModeCmd = new G4UIcmdWithAString("/opnovice2/dimple/mode", this);
  fDimpleModeCmd->SetGuidance("Set dimple shape mode. Week 8.1 supports hemisphere only.");
  fDimpleModeCmd->AvailableForStates(G4State_PreInit);
  fDimpleModeCmd->SetToBeBroadcasted(false);

  fDimpleSiPMModeCmd = new G4UIcmdWithAString("/opnovice2/dimple/sipmMode", this);
  fDimpleSiPMModeCmd->SetGuidance("Set dimple SiPM placement mode: surface or opening.");
  fDimpleSiPMModeCmd->AvailableForStates(G4State_PreInit);
  fDimpleSiPMModeCmd->SetToBeBroadcasted(false);

  fWorldMatPropVectorCmd = new G4UIcmdWithAString("/opnovice2/worldProperty", this);
  fWorldMatPropVectorCmd->SetGuidance("Set material property vector ");
  fWorldMatPropVectorCmd->SetGuidance("for the world.");
  fWorldMatPropVectorCmd->AvailableForStates(G4State_PreInit, G4State_Idle);
  fWorldMatPropVectorCmd->SetToBeBroadcasted(false);

  fWorldMatPropConstCmd = new G4UIcmdWithAString("/opnovice2/worldConstProperty", this);
  fWorldMatPropConstCmd->SetGuidance("Set material constant property");
  fWorldMatPropConstCmd->SetGuidance(" for the world.");
  fWorldMatPropConstCmd->AvailableForStates(G4State_PreInit, G4State_Idle);
  fWorldMatPropConstCmd->SetToBeBroadcasted(false);

  fWorldMaterialCmd = new G4UIcmdWithAString("/opnovice2/worldMaterial", this);
  fWorldMaterialCmd->SetGuidance("Set material of world.");
  fWorldMaterialCmd->AvailableForStates(G4State_PreInit, G4State_Idle);
  fWorldMaterialCmd->SetToBeBroadcasted(false);

  fSiPMFaceCmd = new G4UIcmdWithAString("/opnovice2/sipm/face", this);
  fSiPMFaceCmd->SetGuidance("Set SiPM attached face: +X, -X, +Y, -Y, +Z, -Z, bottomCavity.");
  fSiPMFaceCmd->AvailableForStates(G4State_PreInit);
  fSiPMFaceCmd->SetToBeBroadcasted(false);

  fSiPMCavityModeCmd = new G4UIcmdWithAString("/opnovice2/sipm/cavityMode", this);
  fSiPMCavityModeCmd->SetGuidance("Set bottomCavity SiPM mode: surface or opening.");
  fSiPMCavityModeCmd->AvailableForStates(G4State_PreInit);
  fSiPMCavityModeCmd->SetToBeBroadcasted(false);

  fSiPMLocalPositionCmd =
    new G4UIcmdWith3VectorAndUnit("/opnovice2/sipm/localPosition", this);
  fSiPMLocalPositionCmd->SetGuidance("Set SiPM local center position on selected face.");
  fSiPMLocalPositionCmd->SetGuidance("For +/-X: local x=y, local y=z.");
  fSiPMLocalPositionCmd->SetGuidance("For +/-Y: local x=x, local y=z.");
  fSiPMLocalPositionCmd->SetGuidance("For +/-Z: local x=x, local y=y.");
  fSiPMLocalPositionCmd->SetGuidance("For bottomCavity: local x=x, local y=y.");
  fSiPMLocalPositionCmd->SetParameterName("u", "v", "unused", false);
  fSiPMLocalPositionCmd->SetUnitCategory("Length");
  fSiPMLocalPositionCmd->SetDefaultUnit("cm");
  fSiPMLocalPositionCmd->AvailableForStates(G4State_PreInit);
  fSiPMLocalPositionCmd->SetToBeBroadcasted(false);

  fSiPMSizeCmd = new G4UIcmdWith3VectorAndUnit("/opnovice2/sipm/size", this);
  fSiPMSizeCmd->SetGuidance("Set SiPM size: activeU activeV thickness.");
  fSiPMSizeCmd->SetParameterName("activeU", "activeV", "thickness", false);
  fSiPMSizeCmd->SetUnitCategory("Length");
  fSiPMSizeCmd->SetDefaultUnit("mm");
  fSiPMSizeCmd->AvailableForStates(G4State_PreInit);
  fSiPMSizeCmd->SetToBeBroadcasted(false);
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......

DetectorMessenger::~DetectorMessenger()
{
  delete fOpticalDir;
  delete fSurfaceFinishCmd;
  delete fSurfaceTypeCmd;
  delete fSurfaceModelCmd;
  delete fSurfacePresetCmd;
  delete fSurfaceSigmaAlphaCmd;
  delete fSurfacePolishCmd;
  delete fSurfaceMatPropVectorCmd;
  delete fSurfaceMatPropConstCmd;
  delete fTankMatPropVectorCmd;
  delete fTankMatPropConstCmd;
  delete fTankMaterialCmd;
  delete fTankSizeCmd;
  delete fTankSizePresetCmd;
  delete fTankBottomCavityCmd;
  delete fDimpleEnabledCmd;
  delete fDimpleRadiusCmd;
  delete fDimpleModeCmd;
  delete fDimpleSiPMModeCmd;
  delete fWorldMatPropVectorCmd;
  delete fWorldMatPropConstCmd;
  delete fWorldMaterialCmd;
  delete fSiPMFaceCmd;
  delete fSiPMCavityModeCmd;
  delete fSiPMLocalPositionCmd;
  delete fSiPMSizeCmd;
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......

void DetectorMessenger::SetNewValue(G4UIcommand* command, G4String newValue)
{
  //    FINISH
  if (command == fSurfaceFinishCmd) {
    if (newValue == "polished") {
      fDetector->SetSurfaceFinish(polished);
    }
    else if (newValue == "polishedfrontpainted") {
      fDetector->SetSurfaceFinish(polishedfrontpainted);
    }
    else if (newValue == "polishedbackpainted") {
      fDetector->SetSurfaceFinish(polishedbackpainted);
    }
    else if (newValue == "ground") {
      fDetector->SetSurfaceFinish(ground);
    }
    else if (newValue == "groundfrontpainted") {
      fDetector->SetSurfaceFinish(groundfrontpainted);
    }
    else if (newValue == "groundbackpainted") {
      fDetector->SetSurfaceFinish(groundbackpainted);
    }
    else if (newValue == "polishedlumirrorair") {
      fDetector->SetSurfaceFinish(polishedlumirrorair);
    }
    else if (newValue == "polishedlumirrorglue") {
      fDetector->SetSurfaceFinish(polishedlumirrorglue);
    }
    else if (newValue == "polishedair") {
      fDetector->SetSurfaceFinish(polishedair);
    }
    else if (newValue == "polishedteflonair") {
      fDetector->SetSurfaceFinish(polishedteflonair);
    }
    else if (newValue == "polishedtioair") {
      fDetector->SetSurfaceFinish(polishedtioair);
    }
    else if (newValue == "polishedtyvekair") {
      fDetector->SetSurfaceFinish(polishedtyvekair);
    }
    else if (newValue == "polishedvm2000air") {
      fDetector->SetSurfaceFinish(polishedvm2000air);
    }
    else if (newValue == "polishedvm2000glue") {
      fDetector->SetSurfaceFinish(polishedvm2000glue);
    }
    else if (newValue == "etchedlumirrorair") {
      fDetector->SetSurfaceFinish(etchedlumirrorair);
    }
    else if (newValue == "etchedlumirrorglue") {
      fDetector->SetSurfaceFinish(etchedlumirrorglue);
    }
    else if (newValue == "etchedair") {
      fDetector->SetSurfaceFinish(etchedair);
    }
    else if (newValue == "etchedteflonair") {
      fDetector->SetSurfaceFinish(etchedteflonair);
    }
    else if (newValue == "etchedtioair") {
      fDetector->SetSurfaceFinish(etchedtioair);
    }
    else if (newValue == "etchedtyvekair") {
      fDetector->SetSurfaceFinish(etchedtyvekair);
    }
    else if (newValue == "etchedvm2000air") {
      fDetector->SetSurfaceFinish(etchedvm2000air);
    }
    else if (newValue == "etchedvm2000glue") {
      fDetector->SetSurfaceFinish(etchedvm2000glue);
    }
    else if (newValue == "groundlumirrorair") {
      fDetector->SetSurfaceFinish(groundlumirrorair);
    }
    else if (newValue == "groundlumirrorglue") {
      fDetector->SetSurfaceFinish(groundlumirrorglue);
    }
    else if (newValue == "groundair") {
      fDetector->SetSurfaceFinish(groundair);
    }
    else if (newValue == "groundteflonair") {
      fDetector->SetSurfaceFinish(groundteflonair);
    }
    else if (newValue == "groundtioair") {
      fDetector->SetSurfaceFinish(groundtioair);
    }
    else if (newValue == "groundtyvekair") {
      fDetector->SetSurfaceFinish(groundtyvekair);
    }
    else if (newValue == "groundvm2000air") {
      fDetector->SetSurfaceFinish(groundvm2000air);
    }
    else if (newValue == "groundvm2000glue") {
      fDetector->SetSurfaceFinish(groundvm2000glue);
    }
    //         for Davis model
    else if (newValue == "Rough_LUT") {
      fDetector->SetSurfaceFinish(Rough_LUT);
    }
    else if (newValue == "RoughTeflon_LUT") {
      fDetector->SetSurfaceFinish(RoughTeflon_LUT);
    }
    else if (newValue == "RoughESR_LUT") {
      fDetector->SetSurfaceFinish(RoughESR_LUT);
    }
    else if (newValue == "RoughESRGrease_LUT") {
      fDetector->SetSurfaceFinish(RoughESRGrease_LUT);
    }
    else if (newValue == "Polished_LUT") {
      fDetector->SetSurfaceFinish(Polished_LUT);
    }
    else if (newValue == "PolishedTeflon_LUT") {
      fDetector->SetSurfaceFinish(PolishedTeflon_LUT);
    }
    else if (newValue == "PolishedESR_LUT") {
      fDetector->SetSurfaceFinish(PolishedESR_LUT);
    }
    else if (newValue == "PolishedESRGrease_LUT") {
      fDetector->SetSurfaceFinish(PolishedESRGrease_LUT);
    }
    else if (newValue == "Detector_LUT") {
      fDetector->SetSurfaceFinish(Detector_LUT);
    }
    else {
      G4ExceptionDescription ed;
      ed << "Invalid surface finish: " << newValue;
      G4Exception("DetectorMessenger", "OpNovice2_003", FatalException, ed);
    }
  }

  //  MODEL
  else if (command == fSurfaceModelCmd) {
    if (newValue == "glisur") {
      fDetector->SetSurfaceModel(glisur);
    }
    else if (newValue == "unified") {
      fDetector->SetSurfaceModel(unified);
    }
    else if (newValue == "LUT") {
      fDetector->SetSurfaceModel(LUT);
    }
    else if (newValue == "DAVIS") {
      fDetector->SetSurfaceModel(DAVIS);
    }
    else if (newValue == "dichroic") {
      fDetector->SetSurfaceModel(dichroic);
    }
    else {
      G4ExceptionDescription ed;
      ed << "Invalid surface model: " << newValue;
      G4Exception("DetectorMessenger", "ONovice2_001", FatalException, ed);
    }
  }
  else if (command == fSurfacePresetCmd) {
    fDetector->SetSurfacePreset(newValue);
  }

  // TYPE
  else if (command == fSurfaceTypeCmd) {
    if (newValue == "dielectric_metal") {
      fDetector->SetSurfaceType(dielectric_metal);
    }
    else if (newValue == "dielectric_dielectric") {
      fDetector->SetSurfaceType(dielectric_dielectric);
    }
    else if (newValue == "dielectric_LUT") {
      fDetector->SetSurfaceType(dielectric_LUT);
    }
    else if (newValue == "dielectric_LUTDAVIS") {
      fDetector->SetSurfaceType(dielectric_LUTDAVIS);
    }
    else if (newValue == "coated") {
      fDetector->SetSurfaceType(coated);
    }
    else {
      G4ExceptionDescription ed;
      ed << "Invalid surface type: " << newValue;
      G4Exception("DetectorMessenger", "OpNovice2_002", FatalException, ed);
    }
  }
  else if (command == fSurfaceSigmaAlphaCmd) {
    fDetector->SetSurfaceSigmaAlpha(G4UIcmdWithADouble::GetNewDoubleValue(newValue));
  }
  else if (command == fSurfacePolishCmd) {
    fDetector->SetSurfacePolish(G4UIcmdWithADouble::GetNewDoubleValue(newValue));
  }
  else if (command == fTankMatPropVectorCmd) {
    // got a string. need to convert it to physics vector.
    // string format is property name, then pairs of energy, value
    // specify units for each value, eg 3.0*eV
    // space delimited
    auto mpv = new G4MaterialPropertyVector();
    std::istringstream instring(newValue);
    G4String prop;
    instring >> prop;
    while (instring) {
      G4String tmp;
      instring >> tmp;
      if (tmp == "") {
        break;
      }
      G4double en = G4UIcommand::ConvertToDouble(tmp);
      instring >> tmp;
      G4double val = G4UIcommand::ConvertToDouble(tmp);
      mpv->InsertValues(en, val);
    }

    fDetector->AddTankMPV(prop, mpv);
  }
  else if (command == fWorldMatPropVectorCmd) {
    // Convert string to physics vector
    // string format is property name, then pairs of energy, value
    auto mpv = new G4MaterialPropertyVector();
    std::istringstream instring(newValue);
    G4String prop;
    instring >> prop;
    while (instring) {
      G4String tmp;
      instring >> tmp;
      if (tmp == "") {
        break;
      }
      G4double en = G4UIcommand::ConvertToDouble(tmp);
      instring >> tmp;
      G4double val = G4UIcommand::ConvertToDouble(tmp);
      mpv->InsertValues(en, val);
    }
    fDetector->AddWorldMPV(prop, mpv);
  }
  else if (command == fSurfaceMatPropVectorCmd) {
    // Convert string to physics vector
    // string format is property name, then pairs of energy, value
    // space delimited
    auto mpv = new G4MaterialPropertyVector();
    G4cout << newValue << G4endl;
    std::istringstream instring(newValue);
    G4String prop;
    instring >> prop;
    while (instring) {
      G4String tmp;
      instring >> tmp;
      if (tmp == "") {
        break;
      }
      G4double en = G4UIcommand::ConvertToDouble(tmp);
      instring >> tmp;
      G4double val = G4UIcommand::ConvertToDouble(tmp);
      mpv->InsertValues(en, val);
    }
    fDetector->AddSurfaceMPV(prop, mpv);
  }

  else if (command == fTankMatPropConstCmd) {
    // Convert string to physics vector
    // string format is property name, then value
    // space delimited
    std::istringstream instring(newValue);
    G4String prop;
    G4String tmp;
    instring >> prop;
    instring >> tmp;
    G4double val = G4UIcommand::ConvertToDouble(tmp);
    fDetector->AddTankMPC(prop, val);
  }
  else if (command == fWorldMatPropConstCmd) {
    // Convert string to physics vector
    // string format is property name, then value
    // space delimited
    std::istringstream instring(newValue);
    G4String prop;
    G4String tmp;
    instring >> prop;
    instring >> tmp;
    G4double val = G4UIcommand::ConvertToDouble(tmp);
    fDetector->AddWorldMPC(prop, val);
  }
  else if (command == fSurfaceMatPropConstCmd) {
    // Convert string to physics vector
    // string format is property name, then value
    // space delimited
    std::istringstream instring(newValue);
    G4String prop;
    G4String tmp;
    instring >> prop;
    instring >> tmp;
    G4double val = G4UIcommand::ConvertToDouble(tmp);
    fDetector->AddSurfaceMPC(prop, val);
  }
  else if (command == fWorldMaterialCmd) {
    fDetector->SetWorldMaterial(newValue);
  }
  else if (command == fTankMaterialCmd) {
    fDetector->SetTankMaterial(newValue);
  }
  else if (command == fTankSizeCmd) {
    fDetector->SetTankSize(fTankSizeCmd->GetNew3VectorValue(newValue));
  }
  else if (command == fTankSizePresetCmd) {
    fDetector->SetTankSizePreset(newValue);
  }
  else if (command == fTankBottomCavityCmd) {
    fDetector->SetBottomCavityEnabled(fTankBottomCavityCmd->GetNewBoolValue(newValue));
  }
  else if (command == fDimpleEnabledCmd) {
    fDetector->SetDimpleEnabled(fDimpleEnabledCmd->GetNewBoolValue(newValue));
  }
  else if (command == fDimpleRadiusCmd) {
    fDetector->SetDimpleRadius(fDimpleRadiusCmd->GetNewDoubleValue(newValue));
  }
  else if (command == fDimpleModeCmd) {
    fDetector->SetDimpleMode(newValue);
  }
  else if (command == fDimpleSiPMModeCmd) {
    fDetector->SetDimpleSiPMMode(newValue);
  }

  // --- SiPM commands ---
  else if (command == fSiPMFaceCmd) {
    fDetector->SetSiPMFace(newValue);
  }
  else if (command == fSiPMCavityModeCmd) {
    fDetector->SetSiPMCavityMode(newValue);
  }
  else if (command == fSiPMLocalPositionCmd) {
    fDetector->SetSiPMLocalPosition(
      fSiPMLocalPositionCmd->GetNew3VectorValue(newValue));
  }
  else if (command == fSiPMSizeCmd) {
    fDetector->SetSiPMSize(
      fSiPMSizeCmd->GetNew3VectorValue(newValue));
  }
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
