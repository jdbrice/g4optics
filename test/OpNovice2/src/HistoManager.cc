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
/// \file optical/OpNovice2/src/HistoManager.cc
/// \brief Implementation of the HistoManager class
//
//
//
//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......
//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......

#include "HistoManager.hh"

#include "G4UnitsTable.hh"

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......

HistoManager::HistoManager() : fFileName("opnovice2")
{
  Book();
}

//....oooOO0OOooo........oooOO0OOooo........oooOO0OOooo........oooOO0OOooo......

void HistoManager::Book()
{
  // Create or get analysis manager
  G4AnalysisManager* analysisMan = G4AnalysisManager::Instance();
  analysisMan->SetDefaultFileType("root");
  analysisMan->SetFileName(fFileName);
  analysisMan->SetVerboseLevel(1);
  analysisMan->SetActivation(true);  // enable inactivation of histograms
  analysisMan->SetNtupleMerging(true);

  // Define histograms
  // Default values (to be reset via /analysis/h1/set command)
  G4int n = 100;
  G4double xmn = 0.;
  G4double xmx = 100.;

  // 0
  analysisMan->CreateH1("0", "dummy", n, xmn, xmx);
  // 1
  analysisMan->CreateH1("Cerenkov spectrum", "Cerenkov spectrum", n, xmn, xmx);
  // 2
  analysisMan->CreateH1("Scintillation spectrum", "Scintillation spectrum", n, xmn, xmx);
  // 3
  analysisMan->CreateH1("Scintillation time", "scintillation photons creation time", n, xmn, xmx);
  // 4
  analysisMan->CreateH1("WLS abs", "WLS absorption spectrum", n, xmn, xmx);
  // 5
  analysisMan->CreateH1("WLS em", "WLS emission spectrum", n, xmn, xmx);
  // 6
  analysisMan->CreateH1("WLS time", "WLS emission time", n, xmn, xmx);
  // 7
  analysisMan->CreateH1("WLS2 abs", "WLS2 absorption spectrum", n, xmn, xmx);
  // 8
  analysisMan->CreateH1("WLS2 em", "WLS2 emission spectrum", n, xmn, xmx);
  // 9
  analysisMan->CreateH1("WLS2 time", "WLS2 emission time", n, xmn, xmx);
  // 10
  analysisMan->CreateH1("bdry status", "boundary process status", n, xmn, xmx);
  // 11
  analysisMan->CreateH1("x_backward", "X momentum dir of backward-going photons", n, xmn, xmx);
  // 12
  analysisMan->CreateH1("y_backward", "Y momentum dir of backward-going photons", n, xmn, xmx);
  // 13
  analysisMan->CreateH1("z_backward", "Z momentum dir of backward-going photons", n, xmn, xmx);
  // 14
  analysisMan->CreateH1("x_forward", "X momentum dir of forward-going photons", n, xmn, xmx);
  // 15
  analysisMan->CreateH1("y_forward", "Y momentum dir of forward-going photons", n, xmn, xmx);
  // 16
  analysisMan->CreateH1("z_forward", "Z momentum dir of forward-going photons", n, xmn, xmx);
  // 17
  analysisMan->CreateH1("x_fresnel", "X momentum dir of Fresnel-refracted photons", n, xmn, xmx);
  // 18
  analysisMan->CreateH1("y_fresnel", "Y momentum dir of Fresnel-refracted photons", n, xmn, xmx);
  // 19
  analysisMan->CreateH1("z_fresnel", "Z momentum dir of Fresnel-refracted photons", n, xmn, xmx);
  // 20
  analysisMan->CreateH1("Fresnel refraction", "Fresnel-refracted photons", n, xmn, xmx);
  // 21
  analysisMan->CreateH1("Fresnel reflection", "Fresnel-reflected photons", n, xmn, xmx);
  // 22
  analysisMan->CreateH1("Total internal reflection", "Total internal reflected photons", n, xmn,
                        xmx);
  // 23
  analysisMan->CreateH1("Fresnel reflection plus TIR", "Fresnel-reflected plus TIR photons", n, xmn,
                        xmx);
  // 24
  analysisMan->CreateH1("Absorption", "Absorbed photons", n, xmn, xmx);
  // 25
  analysisMan->CreateH1("Transmitted", "Transmitted photons", n, xmn, xmx);
  // 26
  analysisMan->CreateH1("Spike reflection", "Spike reflected photons", n, xmn, xmx);

  // ----- SiPM histograms -----
  // 27
  analysisMan->CreateH1("SiPM detected spectrum",
                        "Energy spectrum of photons detected by SiPM",
                        n, xmn, xmx);

  // 28
  analysisMan->CreateH1("SiPM detected time",
                        "Arrival time of photons detected by SiPM",
                        n, xmn, xmx);

  // Since this is the first ntuple, its default id value is 0
  analysisMan->CreateNtuple("scan", "Per-event position scan response");
  // I: G4int, D: G4double; if not specified id (i.e. using the signature G4int CreateNtupleXColumn(const G4String& name, std::vector<Xtype>& vector), then the column is added to the last created tuple)
  // https://geant4-userdoc.web.cern.ch/UsersGuides/ForApplicationDeveloper/html/Analysis/managers.html#ntuples
  // The first column will have id 0, and increasing as more columns filling in.
  analysisMan->CreateNtupleIColumn("event_id");
  analysisMan->CreateNtupleDColumn("shoot_x_mm");
  analysisMan->CreateNtupleDColumn("shoot_y_mm");
  analysisMan->CreateNtupleDColumn("shoot_z_mm");
  analysisMan->CreateNtupleIColumn("hit_valid");
  analysisMan->CreateNtupleDColumn("hit_x_mm");
  analysisMan->CreateNtupleDColumn("hit_y_mm");
  analysisMan->CreateNtupleDColumn("hit_z_mm");
  analysisMan->CreateNtupleIColumn("scint_centroid_valid");
  analysisMan->CreateNtupleDColumn("scint_centroid_x_mm");
  analysisMan->CreateNtupleDColumn("scint_centroid_y_mm");
  analysisMan->CreateNtupleDColumn("scint_centroid_z_mm");
  analysisMan->CreateNtupleIColumn("generated_optical_photons");
  analysisMan->CreateNtupleIColumn("scintillation_photons");
  analysisMan->CreateNtupleIColumn("sipm_detected_photons");
  analysisMan->CreateNtupleDColumn("collection_efficiency");
  analysisMan->CreateNtupleDColumn("primary_kinetic_energy_mev");
  // When all ntuple columns are created, the ntuple has to be closed using `FinishNtuple()` function.
  analysisMan->FinishNtuple();

  analysisMan->CreateNtuple("decay_betas", "Radioactive-decay beta electron tracks");
  analysisMan->CreateNtupleIColumn("event_id");
  analysisMan->CreateNtupleIColumn("track_id");
  analysisMan->CreateNtupleSColumn("parent_name");
  analysisMan->CreateNtupleSColumn("creator_process");
  analysisMan->CreateNtupleDColumn("kinetic_energy_mev");
  analysisMan->CreateNtupleDColumn("x_mm");
  analysisMan->CreateNtupleDColumn("y_mm");
  analysisMan->CreateNtupleDColumn("z_mm");
  analysisMan->CreateNtupleDColumn("global_time_ns");
  analysisMan->FinishNtuple();

  for (G4int i = 0; i < analysisMan->GetNofH1s(); ++i) {
    analysisMan->SetH1Activation(i, false);
  }
}
