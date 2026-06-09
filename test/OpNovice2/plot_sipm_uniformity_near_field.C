#include <TFile.h>
#include <TH1.h>
#include <TH2D.h>
#include <TCanvas.h>
#include <TStyle.h>
#include <TString.h>
#include <TGraph.h>
#include <TGraphErrors.h>
#include <TF1.h>

#include <iostream>
#include <vector>
#include <cmath>
#include <algorithm>
#include <utility>

TString LabelInt(int v)
{
  if (v < 0) return Form("m%d", std::abs(v));
  return Form("p%d", v);
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

void plot_sipm_uniformity_near_field()
{
  gStyle->SetOptStat(0);

  const int nEvents = 100;

  // std::vector<double> xs = {-4, 0, 4};
  // std::vector<double> ys = {-4, 0, 4};
  std::vector<int> xs = {-6, -3, 0, 3, 6};
  std::vector<int> ys = {-6, -3, 0, 3, 6};

  // The SiPM location, in the center.
  // If top right w/ right side, x = 5.05, y = 4.85
  const double sipmX = 0.0;
  const double sipmY = 0.0;

  // Store (distance to SiPM, average detected photons/event).
  std::vector<std::pair<double, double>> distPoints;

  // For 3x3: -6.0, 6.0 --- For 9x9: -4.5, 4.5
  TH2D* hMap = new TH2D(
    "hMap",
    "SiPM near-field scan;electron x position [mm];electron y position [mm];#LT N_{SiPM} #GT / event",
    xs.size(), -7.5, 7.5,
    ys.size(), -7.5, 7.5
  );

  for (size_t ix = 0; ix < xs.size(); ++ix) {
    for (size_t iy = 0; iy < ys.size(); ++iy) {
      double x = xs[ix];
      double y = ys[iy];

      TString fname = Form("scan_outputs/sipm_scan_x%smm_y%smm.root",
                           LabelInt(x).Data(),
                           LabelInt(y).Data());

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

      // 2D distance from electron incident position to SiPM center.
      // First-pass approximation: ignore z because the tile is thin.
      double dist = std::sqrt((x - sipmX) * (x - sipmX) +
                              (y - sipmY) * (y - sipmY));
      distPoints.emplace_back(dist, avgDetected);

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

  hMap->GetXaxis()->SetTitle("electron x position [mm]");
  hMap->GetYaxis()->SetTitle("electron y position [mm]");

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

  // ----------------------------
  // Plot response vs distance to SiPM
  // ----------------------------

  // Sort by distance so the graph is ordered from near to far.
  std::sort(distPoints.begin(), distPoints.end(),
            [](const auto& a, const auto& b) {
              return a.first < b.first;
            });

  TGraph* gDist = new TGraph(distPoints.size());

  for (size_t i = 0; i < distPoints.size(); ++i) {
    gDist->SetPoint(i, distPoints[i].first, distPoints[i].second);
  }

  TCanvas* cDist = new TCanvas("cDist", "SiPM response vs distance", 900, 700);

  cDist->SetLeftMargin(0.13);
  cDist->SetRightMargin(0.05);
  cDist->SetBottomMargin(0.13);
  cDist->SetTopMargin(0.10);

  gDist->SetTitle("SiPM response vs distance to SiPM;distance to SiPM [mm];#LT N_{SiPM} #GT / event");
  gDist->SetMarkerStyle(20);
  gDist->SetMarkerSize(0.9);

  // Use markers only first. Connecting all points can look messy because
  // multiple (x,y) positions can have similar distances.
  gDist->Draw("AP");

  cDist->SaveAs("sipm_response_vs_distance.pdf");
  cDist->SaveAs("sipm_response_vs_distance.png");

  // ----------------------------
  // Distance-bin averaged response
  // ----------------------------

  const double binWidth = 1.0;   // cm
  const double rMin = 0.0;
  const double rMax = 14.0;      // enough for current 10 cm x 10 cm tile
  const int nRBins = int((rMax - rMin) / binWidth);

  std::vector<int> counts(nRBins, 0);
  std::vector<double> sumY(nRBins, 0.0);
  std::vector<double> sumY2(nRBins, 0.0);

  for (const auto& p : distPoints) {
    double r = p.first;
    double y = p.second;

    int ibin = int((r - rMin) / binWidth);
    if (ibin < 0 || ibin >= nRBins) continue;

    counts[ibin] += 1;
    sumY[ibin] += y;
    sumY2[ibin] += y * y;
  }

  std::vector<double> rCenters;
  std::vector<double> yMeans;
  std::vector<double> rErrors;
  std::vector<double> yErrors;

  for (int i = 0; i < nRBins; ++i) {
    if (counts[i] == 0) continue;

    double mean = sumY[i] / counts[i];
    double mean2 = sumY2[i] / counts[i];

    double rms = 0.0;
    if (counts[i] > 1) {
      rms = std::sqrt(std::max(0.0, mean2 - mean * mean));
    }

    double rCenter = rMin + (i + 0.5) * binWidth;

    rCenters.push_back(rCenter);
    yMeans.push_back(mean);
    rErrors.push_back(0.5 * binWidth);

    // This error bar is the spread of points in the distance bin,
    // not pure statistical uncertainty.
    yErrors.push_back(rms);
  }

  TGraphErrors* gAvg = new TGraphErrors(rCenters.size());

  for (size_t i = 0; i < rCenters.size(); ++i) {
    gAvg->SetPoint(i, rCenters[i], yMeans[i]);
    gAvg->SetPointError(i, rErrors[i], yErrors[i]);
  }

  TCanvas* cAvg = new TCanvas("cAvg", "Distance-binned SiPM response", 900, 700);

  cAvg->SetLeftMargin(0.13);
  cAvg->SetRightMargin(0.05);
  cAvg->SetBottomMargin(0.13);
  cAvg->SetTopMargin(0.10);

  gAvg->SetTitle("Distance-binned SiPM response;distance to SiPM [mm];#LT N_{SiPM} #GT / event");
  gAvg->SetMarkerStyle(20);
  gAvg->SetMarkerSize(1.0);
  gAvg->Draw("AP");

  // Empirical exponential fit.
  // This is only a trend guide, not a full optical transport model.
  TF1* fExp = new TF1("fExp", "[0]*exp(-x/[1]) + [2]", 0.0, 14.0);
  fExp->SetParNames("A", "lambda_eff", "C");
  fExp->SetParameters(700.0, 8.0, 300.0);

  gAvg->Fit(fExp, "R");
  fExp->Draw("same");

  cAvg->SaveAs("sipm_response_vs_distance_binned.pdf");
  cAvg->SaveAs("sipm_response_vs_distance_binned.png");
}