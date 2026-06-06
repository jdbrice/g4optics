#include <TFile.h>
#include <TH1.h>
#include <TH2D.h>
#include <TCanvas.h>
#include <TStyle.h>
#include <TString.h>

#include <iostream>
#include <vector>
#include <cmath>

TString LabelNum(double v)
{
  if (v < 0) return Form("m%.0f", std::abs(v));
  return Form("p%.0f", v);
}

TH1* GetDetectedHist(TFile& f)
{
  // Geant4 ROOT object names can vary slightly depending on analysis setup.
  // Try the most likely names.
  const char* candidates[] = {
    "SiPM detected spectrum",
    "h27",
    "27"
  };

  for (auto name : candidates) {
    auto h = dynamic_cast<TH1*>(f.Get(name));
    if (h) return h;
  }

  f.ls();
  return nullptr;
}

void plot_sipm_uniformity()
{
  gStyle->SetOptStat(0);

  const int nEvents = 100;

  std::vector<double> xs = {-4, 0, 4};
  std::vector<double> ys = {-4, 0, 4};

  TH2D* hMap = new TH2D(
    "hMap",
    "SiPM collection uniformity;electron x position [cm];electron y position [cm];#LT detected photons / event #GT",
    xs.size(), -6, 6,
    ys.size(), -6, 6
  );

  for (size_t ix = 0; ix < xs.size(); ++ix) {
    for (size_t iy = 0; iy < ys.size(); ++iy) {
      double x = xs[ix];
      double y = ys[iy];

      TString fname = Form("scan_outputs/sipm_scan_x%s_y%s.root",
                           LabelNum(x).Data(),
                           LabelNum(y).Data());

      TFile f(fname, "READ");
      if (f.IsZombie()) {
        std::cerr << "Cannot open " << fname << std::endl;
        continue;
      }

      TH1* hDet = GetDetectedHist(f);
      if (!hDet) {
        std::cerr << "Cannot find detected photon histogram in " << fname << std::endl;
        continue;
      }

      double avgDetected = hDet->GetEntries() / double(nEvents);

      int bx = hMap->GetXaxis()->FindBin(x);
      int by = hMap->GetYaxis()->FindBin(y);
      hMap->SetBinContent(bx, by, avgDetected);
    }
  }

  gStyle->SetOptStat(0);
  gStyle->SetPaintTextFormat(".2f");

  TCanvas* c = new TCanvas("c", "SiPM uniformity", 1100, 850);

  // Give enough space for axis titles and color bar title.
  c->SetLeftMargin(0.12);
  c->SetRightMargin(0.18);
  c->SetBottomMargin(0.12);
  c->SetTopMargin(0.10);

  hMap->SetTitle("SiPM collection uniformity");

  hMap->GetXaxis()->SetTitle("electron x position [cm]");
  hMap->GetYaxis()->SetTitle("electron y position [cm]");

  // Shorter and cleaner Z title.
  // This is the color-bar title.
  hMap->GetZaxis()->SetTitle("#LT N_{SiPM} #GT / event");
  hMap->GetZaxis()->SetTitleOffset(1.25);
  hMap->GetZaxis()->SetTitleSize(0.040);
  hMap->GetZaxis()->SetLabelSize(0.040);

  hMap->GetXaxis()->SetTitleSize(0.045);
  hMap->GetYaxis()->SetTitleSize(0.045);
  hMap->GetXaxis()->SetLabelSize(0.040);
  hMap->GetYaxis()->SetLabelSize(0.040);

  hMap->Draw("COLZ TEXT");

  c->SaveAs("sipm_uniformity_map.pdf");
  c->SaveAs("sipm_uniformity_map.png");
}