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
/// \file optical/OpNovice2/include/DetectorConstruction.hh
/// \brief Definition of the DetectorConstruction class
//
//
//
//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......

#ifndef DetectorConstruction_h
#define DetectorConstruction_h 1

#include "G4OpticalSurface.hh"
#include "G4RunManager.hh"
#include "G4VUserDetectorConstruction.hh"
#include "G4ThreeVector.hh"
#include "globals.hh"

#include <CLHEP/Units/SystemOfUnits.h>

class DetectorMessenger;

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......

class DetectorConstruction : public G4VUserDetectorConstruction
{
  public:
    DetectorConstruction();
    ~DetectorConstruction() override;

    G4VPhysicalVolume* Construct() override;

    G4VPhysicalVolume* GetTank() { return fTank; }
    G4double GetTankXSize() { return fTank_x; }

    G4OpticalSurface* GetSurface(void) { return fSurface; }

    void SetSurfaceFinish(const G4OpticalSurfaceFinish finish)
    {
      fSurface->SetFinish(finish);
      G4RunManager::GetRunManager()->GeometryHasBeenModified();
    }
    G4OpticalSurfaceFinish GetSurfaceFinish() { return fSurface->GetFinish(); }

    void SetSurfaceType(const G4SurfaceType type)
    {
      fSurface->SetType(type);
      G4RunManager::GetRunManager()->GeometryHasBeenModified();
    }

    void SetSurfaceModel(const G4OpticalSurfaceModel model)
    {
      fSurface->SetModel(model);
      G4RunManager::GetRunManager()->GeometryHasBeenModified();
    }
    G4OpticalSurfaceModel GetSurfaceModel() { return fSurface->GetModel(); }

    void SetSurfaceSigmaAlpha(G4double v);
    void SetSurfacePolish(G4double v);
    void SetSurfacePreset(const G4String& preset);

    void AddTankMPV(const G4String& prop, G4MaterialPropertyVector* mpv);
    void AddTankMPC(const G4String& prop, G4double v);
    G4MaterialPropertiesTable* GetTankMaterialPropertiesTable() { return fTankMPT; }

    void AddWorldMPV(const G4String& prop, G4MaterialPropertyVector* mpv);
    void AddWorldMPC(const G4String& prop, G4double v);
    G4MaterialPropertiesTable* GetWorldMaterialPropertiesTable() { return fWorldMPT; }

    void AddSurfaceMPV(const G4String& prop, G4MaterialPropertyVector* mpv);
    void AddSurfaceMPC(const G4String& prop, G4double v);
    G4MaterialPropertiesTable* GetSurfaceMaterialPropertiesTable() { return fSurfaceMPT; }

    void SetGreaseEnabled(G4bool enabled);
    void SetGreaseThickness(G4double thickness);
    void SetGreaseSize(const G4ThreeVector& size);
    void AddGreaseMPV(const G4String& prop, G4MaterialPropertyVector* mpv);
    void AddGreaseMPC(const G4String& prop, G4double v);
    G4MaterialPropertiesTable* GetGreaseMaterialPropertiesTable() { return fGreaseMPT; }

    void SetWorldMaterial(const G4String&);
    G4Material* GetWorldMaterial() const { return fWorldMaterial; }
    void SetTankMaterial(const G4String&);
    G4Material* GetTankMaterial() const { return fTankMaterial; }
    void SetTankSize(const G4ThreeVector& fullSize);
    void SetTankSizePreset(const G4String& preset);
    void SetBottomCavityEnabled(G4bool enabled);
    void SetDimpleEnabled(G4bool enabled);
    void SetDimpleRadius(G4double radius);
    void SetDimpleMode(const G4String& mode);
    void SetDimpleSiPMMode(const G4String& mode);

    // setting SiPM
    void SetSiPMFace(const G4String& face);
    void SetSiPMCavityMode(const G4String& mode);
    void SetSiPMLocalPosition(const G4ThreeVector& pos);
    void SetSiPMSize(const G4ThreeVector& size);

  private:
    G4double fExpHall_x = 50. * CLHEP::cm;
    G4double fExpHall_y = 50. * CLHEP::cm;
    G4double fExpHall_z = 50. * CLHEP::cm;

    G4VPhysicalVolume* fTank = nullptr;

    G4double fTank_x = 5. * CLHEP::cm;
    G4double fTank_y = 5. * CLHEP::cm;
    G4double fTank_z = .25 * CLHEP::cm;
    G4bool fBottomCavityEnabled = false;
    G4bool fDimpleEnabled = false;
    G4double fDimpleRadius = 3. * CLHEP::mm;
    G4String fDimpleMode = "hemisphere";
    G4String fDimpleSiPMMode = "surface";
    G4bool fDimpleSiPMModeExplicit = false;

    G4LogicalVolume* fWorld_LV = nullptr;
    G4LogicalVolume* fTank_LV = nullptr;

    G4Material* fWorldMaterial = nullptr;
    G4Material* fTankMaterial = nullptr;

    G4OpticalSurface* fSurface = nullptr;

    DetectorMessenger* fDetectorMessenger = nullptr;

    G4MaterialPropertiesTable* fTankMPT = nullptr;
    G4MaterialPropertiesTable* fWorldMPT = nullptr;
    G4MaterialPropertiesTable* fSurfaceMPT = nullptr;

    /// Adding SiPM
    // Physical Volume & Logical Volume
    G4VPhysicalVolume* fSiPM = nullptr;
    G4LogicalVolume* fSiPM_LV = nullptr;

    // Material & Properties Table
    G4Material* fSiPMMaterial = nullptr;
    G4MaterialPropertiesTable* fSiPMMPT = nullptr;

    // Optional EJ-550 coupling: flat pad or curved dimple-to-SiPM gap.
    G4VPhysicalVolume* fGrease = nullptr;
    G4LogicalVolume* fGrease_LV = nullptr;
    G4Material* fGreaseMaterial = nullptr;
    G4MaterialPropertiesTable* fGreaseMPT = nullptr;
    G4bool fGreaseEnabled = false;
    G4double fGreaseThickness = 0.0;
    G4double fGreaseActiveU = 0.0;
    G4double fGreaseActiveV = 0.0;

    // General SiPM placement.
    // fSiPMFace controls which tile face the SiPM is attached to.
    // Accepted values: +X, -X, +Y, -Y, +Z, -Z, bottomCavity.
    G4String fSiPMFace = "+X";
    G4String fSiPMCavityMode = "surface";
    G4bool fSiPMCavityModeExplicit = false;

    // Local position on the selected face.
    // For +/-X: local x = y, local y = z.
    // For +/-Y: local x = x, local y = z.
    // For +/-Z: local x = x, local y = y.
    // The third component is currently unused.
    G4ThreeVector fSiPMLocalPosition =
      G4ThreeVector(4.85 * CLHEP::cm, 0.0, 0.0);

    // SiPM physical dimensions:
    // activeU and activeV are the active face dimensions;
    // thickness is normal to the selected face.
    G4double fSiPMActiveU = 3.0 * CLHEP::mm;
    G4double fSiPMActiveV = 3.0 * CLHEP::mm;
    G4double fSiPMThickness = 1.0 * CLHEP::mm;

    void ComputeSiPMPlacement(G4double& hx,
                              G4double& hy,
                              G4double& hz,
                              G4ThreeVector& pos) const;
    G4double GetBottomCavityRadius() const;
    G4String GetEffectiveDimpleSiPMMode() const;
    G4double GetSiPMFootprintCornerRadius(G4double u,
                                          G4double v,
                                          G4double hu,
                                          G4double hv) const;
    G4double GetGreaseActiveU() const;
    G4double GetGreaseActiveV() const;
    void ComputeGreasePlacement(G4double& hx,
                                G4double& hy,
                                G4double& hz,
                                G4ThreeVector& pos) const;
    void ValidateDimpleConfiguration() const;
    void ValidateGreaseConfiguration() const;
    void ResetSurfaceMaterialPropertiesTable();
};

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......

#endif /*DetectorConstruction_h*/
