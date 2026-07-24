#ifndef OPNOVICE2_PLOT_LAB_V2_SURFACE_C
#define OPNOVICE2_PLOT_LAB_V2_SURFACE_C

#include "plot_efficiency_map.C"

#include <TAxis.h>
#include <TCanvas.h>
#include <TGraph.h>
#include <TGraphErrors.h>
#include <TH1D.h>
#include <TLegend.h>
#include <TPaveText.h>
#include <TStyle.h>
#include <TSystem.h>

#include <algorithm>
#include <cmath>
#include <fstream>
#include <map>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

const std::vector<std::string> kSurfaceOrder = {
  "polishedfrontpainted",
  "groundfrontpainted",
  "polishedbackpainted",
  "groundbackpainted"
};

const std::vector<std::string> kSampleOrder = {
  "11p5x11p5x16", "11p5x11p5x4", "5x5x16", "5x5x4"
};

struct SurfaceProfilePoint {
  std::string sampleId;
  std::string sampleTitle;
  std::string surface;
  double divergence = kNaN;
  int pointIndex = 0;
  bool inside = false;
  double lab = kNaN;
  double uncertainty = kNaN;
  double scaledSim = kNaN;
  double allChi2Ndf = kNaN;
  double allRmse = kNaN;
  double allScaledRmse = kNaN;
  double insideChi2Ndf = kNaN;
  double insideRmse = kNaN;
  double insideScaledRmse = kNaN;
};

struct SurfaceGlobalMetric {
  std::string surface;
  std::string region;
  double meanSampleChi2Ndf = kNaN;
  double globalChi2Ndf = kNaN;
  double meanSampleRmse = kNaN;
  double meanSampleScaledRmse = kNaN;
};

struct SurfaceSampleMetric {
  std::string sampleId;
  std::string surface;
  std::string region;
  double chi2Ndf = kNaN;
  double scaledRmse = kNaN;
};

std::map<std::string, int> SurfaceHeader(const std::string& line)
{
  std::map<std::string, int> columns;
  const auto fields = SplitCsvLine(line);
  for (size_t i = 0; i < fields.size(); ++i) {
    columns[Trim(fields[i])] = static_cast<int>(i);
  }
  return columns;
}

std::vector<SurfaceProfilePoint> ReadSurfaceProfiles(const TString& path)
{
  std::ifstream in(path.Data());
  if (!in) throw std::runtime_error(std::string("Cannot open profile CSV: ") + path.Data());
  std::string line;
  if (!std::getline(in, line)) throw std::runtime_error("Empty profile CSV");
  const auto columns = SurfaceHeader(line);
  const int sampleCol = ColumnIndex(columns, "sample_id");
  const int titleCol = ColumnIndex(columns, "sample_title");
  const int surfaceCol = ColumnIndex(columns, "surface_preset");
  const int divergenceCol = ColumnIndex(columns, "divergence_mrad");
  const int indexCol = ColumnIndex(columns, "point_index");
  const int insideCol = ColumnIndex(columns, "inside_tile");
  const int labCol = ColumnIndex(columns, "lab_response");
  const int uncertaintyCol = ColumnIndex(columns, "lab_uncertainty");
  const int simCol = ColumnIndex(columns, "scaled_sim_sample");
  const int allChi2Col = ColumnIndex(columns, "all_chi2_ndf");
  const int allRmseCol = ColumnIndex(columns, "all_normalized_rmse");
  const int allScaledRmseCol = ColumnIndex(columns, "all_scaled_normalized_rmse");
  const int insideChi2Col = ColumnIndex(columns, "inside_chi2_ndf");
  const int insideRmseCol = ColumnIndex(columns, "inside_normalized_rmse");
  const int insideScaledRmseCol = ColumnIndex(columns, "inside_scaled_normalized_rmse");

  std::vector<SurfaceProfilePoint> rows;
  while (std::getline(in, line)) {
    if (Trim(line).empty()) continue;
    auto fields = SplitCsvLine(line);
    if (fields.size() < columns.size()) fields.resize(columns.size());
    SurfaceProfilePoint row;
    row.sampleId = Trim(fields[sampleCol]);
    row.sampleTitle = Trim(fields[titleCol]);
    row.surface = Trim(fields[surfaceCol]);
    row.divergence = ParseDouble(fields[divergenceCol]);
    row.pointIndex = static_cast<int>(ParseDouble(fields[indexCol]));
    row.inside = ParseDouble(fields[insideCol]) != 0.0;
    row.lab = ParseDouble(fields[labCol]);
    row.uncertainty = ParseDouble(fields[uncertaintyCol]);
    row.scaledSim = ParseDouble(fields[simCol]);
    row.allChi2Ndf = ParseDouble(fields[allChi2Col]);
    row.allRmse = ParseDouble(fields[allRmseCol]);
    row.allScaledRmse = ParseDouble(fields[allScaledRmseCol]);
    row.insideChi2Ndf = ParseDouble(fields[insideChi2Col]);
    row.insideRmse = ParseDouble(fields[insideRmseCol]);
    row.insideScaledRmse = ParseDouble(fields[insideScaledRmseCol]);
    rows.push_back(row);
  }
  return rows;
}

std::vector<SurfaceGlobalMetric> ReadSurfaceGlobalMetrics(const TString& path)
{
  std::ifstream in(path.Data());
  if (!in) throw std::runtime_error(std::string("Cannot open global metrics: ") + path.Data());
  std::string line;
  if (!std::getline(in, line)) throw std::runtime_error("Empty global metrics CSV");
  const auto columns = SurfaceHeader(line);
  const int surfaceCol = ColumnIndex(columns, "surface_preset");
  const int regionCol = ColumnIndex(columns, "region");
  const int sampleChi2Col = ColumnIndex(columns, "mean_sample_chi2_ndf");
  const int globalChi2Col = ColumnIndex(columns, "global_chi2_ndf");
  const int rmseCol = ColumnIndex(columns, "mean_sample_normalized_rmse");
  const int scaledRmseCol = ColumnIndex(columns, "mean_sample_scaled_normalized_rmse");

  std::vector<SurfaceGlobalMetric> rows;
  while (std::getline(in, line)) {
    if (Trim(line).empty()) continue;
    auto fields = SplitCsvLine(line);
    if (fields.size() < columns.size()) fields.resize(columns.size());
    SurfaceGlobalMetric row;
    row.surface = Trim(fields[surfaceCol]);
    row.region = Trim(fields[regionCol]);
    row.meanSampleChi2Ndf = ParseDouble(fields[sampleChi2Col]);
    row.globalChi2Ndf = ParseDouble(fields[globalChi2Col]);
    row.meanSampleRmse = ParseDouble(fields[rmseCol]);
    row.meanSampleScaledRmse = ParseDouble(fields[scaledRmseCol]);
    rows.push_back(row);
  }
  return rows;
}

std::vector<SurfaceSampleMetric> ReadSurfaceSampleMetrics(const TString& path)
{
  std::ifstream in(path.Data());
  if (!in) throw std::runtime_error(std::string("Cannot open sample metrics: ") + path.Data());
  std::string line;
  if (!std::getline(in, line)) throw std::runtime_error("Empty sample metrics CSV");
  const auto columns = SurfaceHeader(line);
  const int sampleCol = ColumnIndex(columns, "sample_id");
  const int surfaceCol = ColumnIndex(columns, "surface_preset");
  const int regionCol = ColumnIndex(columns, "region");
  const int chi2Col = ColumnIndex(columns, "chi2_ndf");
  const int scaledRmseCol = ColumnIndex(columns, "scaled_normalized_rmse");

  std::vector<SurfaceSampleMetric> rows;
  while (std::getline(in, line)) {
    if (Trim(line).empty()) continue;
    auto fields = SplitCsvLine(line);
    if (fields.size() < columns.size()) fields.resize(columns.size());
    SurfaceSampleMetric row;
    row.sampleId = Trim(fields[sampleCol]);
    row.surface = Trim(fields[surfaceCol]);
    row.region = Trim(fields[regionCol]);
    row.chi2Ndf = ParseDouble(fields[chi2Col]);
    row.scaledRmse = ParseDouble(fields[scaledRmseCol]);
    rows.push_back(row);
  }
  return rows;
}

TString JoinSurfacePath(TString base, const char* child)
{
  if (base.EndsWith("/")) base.Chop();
  return Form("%s/%s", base.Data(), child);
}

std::string ShortSurfaceLabel(const std::string& surface)
{
  if (surface == "polishedfrontpainted") return "polished front";
  if (surface == "groundfrontpainted") return "ground front";
  if (surface == "polishedbackpainted") return "polished back";
  if (surface == "groundbackpainted") return "ground back";
  return surface;
}

std::string ShortSampleLabel(const std::string& sampleId)
{
  if (sampleId == "11p5x11p5x16") return "11.5x11.5x1.6 cm";
  if (sampleId == "11p5x11p5x4") return "11.5x11.5x0.4 cm";
  if (sampleId == "5x5x16") return "5x5x1.6 cm";
  if (sampleId == "5x5x4") return "5x5x0.4 cm";
  return sampleId;
}

std::vector<std::string> ActiveSamples(const std::vector<SurfaceProfilePoint>& rows)
{
  std::vector<std::string> active;
  for (const auto& sampleId : kSampleOrder) {
    const bool present = std::any_of(rows.begin(), rows.end(), [&](const auto& row) {
      return row.sampleId == sampleId;
    });
    if (present) active.push_back(sampleId);
  }
  return active;
}

void DivideForSamples(TCanvas* canvas, size_t sampleCount)
{
  if (sampleCount == 0) return;
  if (sampleCount <= 2) {
    canvas->Divide(static_cast<int>(sampleCount), 1, 0.002, 0.002);
  } else {
    canvas->Divide(2, 2, 0.002, 0.002);
  }
}

double LabMaximum(const std::vector<SurfaceProfilePoint>& points)
{
  double maximum = 0.0;
  for (const auto& point : points) {
    if (std::isfinite(point.lab)) maximum = std::max(maximum, std::abs(point.lab));
  }
  return maximum;
}

void ConfigurePad()
{
  gPad->SetLeftMargin(0.13);
  gPad->SetRightMargin(0.28);
  gPad->SetBottomMargin(0.13);
  gPad->SetTopMargin(0.12);
  gPad->SetGrid(1, 1);
  gPad->SetFillColor(kWhite);
}

void DrawSurfaceQuadrantPanel(const std::vector<SurfaceProfilePoint>& points)
{
  if (points.empty()) return;
  const double labMax = LabMaximum(points);
  auto* labGraph = new TGraphErrors();
  auto* simGraph = new TGraph();
  auto* outsideLab = new TGraphErrors();
  double yMin = 0.0;
  double yMax = 0.0;
  int nLab = 0;
  int nOutside = 0;
  for (const auto& point : points) {
    if (!std::isfinite(point.lab) || !std::isfinite(point.scaledSim) || labMax <= 0.0) continue;
    const double labY = 100.0 * point.lab / labMax;
    const double labError = std::isfinite(point.uncertainty) ? 100.0 * point.uncertainty / labMax : 0.0;
    const double simY = 100.0 * point.scaledSim / labMax;
    labGraph->SetPoint(nLab, point.pointIndex, labY);
    labGraph->SetPointError(nLab, 0.0, labError);
    simGraph->SetPoint(nLab, point.pointIndex, simY);
    ++nLab;
    if (!point.inside) {
      outsideLab->SetPoint(nOutside, point.pointIndex, labY);
      outsideLab->SetPointError(nOutside, 0.0, labError);
      ++nOutside;
    }
    yMin = std::min(yMin, labY - labError);
    yMax = std::max(yMax, std::max(labY + labError, simY));
  }

  ConfigurePad();
  const double span = yMax - yMin;
  const double pad = span > 0.0 ? 0.12 * span : 10.0;
  auto* frame = gPad->DrawFrame(
    0.5,
    yMin - pad,
    points.size() + 0.5,
    yMax + pad,
    Form("%s, %s, %g mrad;lab scan point index;response / lab max [%%]",
         points.front().sampleTitle.c_str(),
         ShortSurfaceLabel(points.front().surface).c_str(),
         points.front().divergence));
  frame->GetYaxis()->SetTitleOffset(0.90);
  frame->GetXaxis()->SetNdivisions(points.size() > 20 ? 14 : static_cast<int>(points.size()));

  labGraph->SetMarkerStyle(20);
  labGraph->SetMarkerSize(0.78);
  labGraph->SetMarkerColor(kBlack);
  labGraph->SetLineColor(kBlack);
  labGraph->Draw("P same");
  simGraph->SetMarkerStyle(24);
  simGraph->SetMarkerSize(0.80);
  simGraph->SetMarkerColor(kRed + 1);
  simGraph->SetLineColor(kRed + 1);
  simGraph->SetLineWidth(2);
  simGraph->Draw("LP same");
  outsideLab->SetMarkerStyle(21);
  outsideLab->SetMarkerColor(kBlue + 1);
  outsideLab->SetLineColor(kBlue + 1);
  outsideLab->Draw("P same");

  auto* legend = new TLegend(0.73, 0.72, 0.985, 0.90);
  legend->SetBorderSize(0);
  legend->SetFillColorAlpha(kWhite, 0.90);
  legend->SetTextSize(0.025);
  legend->AddEntry(labGraph, "lab (inside)", "pe");
  legend->AddEntry(outsideLab, "lab (outside tile)", "pe");
  legend->AddEntry(simGraph, "simulation, per-tile scale", "lp");
  legend->Draw();

  const auto& first = points.front();
  auto* note = new TPaveText(0.73, 0.43, 0.985, 0.68, "NDC");
  note->SetFillColorAlpha(kWhite, 0.90);
  note->SetBorderSize(0);
  note->SetTextAlign(12);
  note->SetTextSize(0.024);
  note->AddText(Form("all: #chi^{2}/ndf = %.2f", first.allChi2Ndf));
  note->AddText(Form("all: scaled NRMSE = %.1f%%", 100.0 * first.allScaledRmse));
  note->AddText(Form("inside: #chi^{2}/ndf = %.2f", first.insideChi2Ndf));
  note->AddText(Form("inside: scaled NRMSE = %.1f%%", 100.0 * first.insideScaledRmse));
  note->Draw();
}

double GlobalValue(const std::vector<SurfaceGlobalMetric>& rows,
                   const std::string& surface,
                   const std::string& region,
                   double SurfaceGlobalMetric::*field)
{
  for (const auto& row : rows) {
    if (row.surface == surface && row.region == region) return row.*field;
  }
  return kNaN;
}

void DrawSurfaceBars(const std::vector<SurfaceGlobalMetric>& rows,
                     const char* title,
                     const char* yTitle,
                     double SurfaceGlobalMetric::*field,
                     double multiplier)
{
  static int histogramIndex = 0;
  ++histogramIndex;
  auto* all = new TH1D(
    Form("h_surface_all_%d", histogramIndex),
    Form("%s;surface;%s", title, yTitle),
    4,
    0.5,
    4.5);
  auto* inside = new TH1D(
    Form("h_surface_inside_%d", histogramIndex), "", 4, 0.5, 4.5);
  double maximum = 0.0;
  for (size_t i = 0; i < kSurfaceOrder.size(); ++i) {
    const double allValue = multiplier * GlobalValue(rows, kSurfaceOrder[i], "all", field);
    const double insideValue = multiplier * GlobalValue(rows, kSurfaceOrder[i], "inside", field);
    all->SetBinContent(static_cast<int>(i + 1), allValue);
    inside->SetBinContent(static_cast<int>(i + 1), insideValue);
    all->GetXaxis()->SetBinLabel(static_cast<int>(i + 1), ShortSurfaceLabel(kSurfaceOrder[i]).c_str());
    maximum = std::max(maximum, std::max(allValue, insideValue));
  }
  gPad->SetLeftMargin(0.13);
  gPad->SetRightMargin(0.04);
  gPad->SetBottomMargin(0.18);
  gPad->SetTopMargin(0.11);
  gPad->SetGrid(0, 1);
  gPad->SetFillColor(kWhite);
  all->SetMaximum(1.18 * maximum);
  all->SetMinimum(0.0);
  all->SetFillColor(kGray + 1);
  all->SetBarWidth(0.34);
  all->SetBarOffset(0.10);
  all->GetYaxis()->SetTitleOffset(0.90);
  all->LabelsOption("v", "X");
  all->Draw("BAR2");
  inside->SetFillColor(kBlue + 1);
  inside->SetBarWidth(0.34);
  inside->SetBarOffset(0.53);
  inside->Draw("BAR2 same");
  auto* legend = new TLegend(0.69, 0.78, 0.94, 0.91);
  legend->SetBorderSize(0);
  legend->SetFillColorAlpha(kWhite, 0.90);
  legend->AddEntry(all, "all points", "f");
  legend->AddEntry(inside, "inside tile", "f");
  legend->Draw();
}

void DrawPerSampleSurfaceScaledRmse(const std::vector<SurfaceSampleMetric>& rows,
                                    const std::vector<std::string>& sampleOrder,
                                    double divergenceMrad)
{
  const Color_t colors[] = {kBlue + 1, kOrange + 7, kMagenta + 1, kGreen + 2};
  const Style_t markers[] = {20, 21, 22, 23};
  std::vector<TGraph*> graphs;
  double maximum = 0.0;
  for (size_t sampleIndex = 0; sampleIndex < sampleOrder.size(); ++sampleIndex) {
    auto* graph = new TGraph();
    for (size_t surfaceIndex = 0; surfaceIndex < kSurfaceOrder.size(); ++surfaceIndex) {
      for (const auto& row : rows) {
        if (row.sampleId == sampleOrder[sampleIndex]
            && row.surface == kSurfaceOrder[surfaceIndex]
            && row.region == "all"
            && std::isfinite(row.scaledRmse)) {
          graph->SetPoint(graph->GetN(), surfaceIndex + 1.0, 100.0 * row.scaledRmse);
          maximum = std::max(maximum, 100.0 * row.scaledRmse);
        }
      }
    }
    graph->SetLineColor(colors[sampleIndex]);
    graph->SetMarkerColor(colors[sampleIndex]);
    graph->SetMarkerStyle(markers[sampleIndex]);
    graph->SetMarkerSize(1.0);
    graph->SetLineWidth(2);
    graphs.push_back(graph);
  }
  gPad->SetLeftMargin(0.13);
  gPad->SetRightMargin(0.28);
  gPad->SetBottomMargin(0.18);
  gPad->SetTopMargin(0.11);
  gPad->SetGrid(1, 1);
  gPad->SetFillColor(kWhite);
  if (maximum <= 0.0) maximum = 1.0;
  auto* frame = gPad->DrawFrame(
    0.5, 0.0, 4.5, 1.15 * maximum,
    Form("Per-tile scaled residual, all points (%g mrad);surface;scaled normalized RMSE [%%]",
         divergenceMrad));
  frame->GetYaxis()->SetTitleOffset(0.90);
  for (size_t i = 0; i < kSurfaceOrder.size(); ++i) {
    frame->GetXaxis()->SetBinLabel(
      frame->GetXaxis()->FindBin(i + 1.0),
      ShortSurfaceLabel(kSurfaceOrder[i]).c_str());
  }
  frame->GetXaxis()->LabelsOption("v");
  auto* legend = new TLegend(0.73, 0.72, 0.985, 0.90);
  legend->SetBorderSize(0);
  legend->SetFillColorAlpha(kWhite, 0.90);
  for (size_t i = 0; i < graphs.size(); ++i) {
    graphs[i]->Draw("LP same");
    legend->AddEntry(graphs[i], ShortSampleLabel(sampleOrder[i]).c_str(), "lp");
  }
  legend->Draw();
}

void DrawCrossSurfacePanel(const std::vector<SurfaceProfilePoint>& rows,
                           const std::string& sampleId)
{
  std::vector<SurfaceProfilePoint> reference;
  std::map<std::string, std::vector<SurfaceProfilePoint>> bySurface;
  for (const auto& row : rows) {
    if (row.sampleId != sampleId) continue;
    bySurface[row.surface].push_back(row);
    if (row.surface == kSurfaceOrder.front()) reference.push_back(row);
  }
  if (reference.empty()) return;
  const auto byIndex = [](const SurfaceProfilePoint& a, const SurfaceProfilePoint& b) {
    return a.pointIndex < b.pointIndex;
  };
  std::sort(reference.begin(), reference.end(), byIndex);
  for (auto& entry : bySurface) std::sort(entry.second.begin(), entry.second.end(), byIndex);
  const double labMax = LabMaximum(reference);
  auto* labGraph = new TGraphErrors();
  auto* outsideLab = new TGraphErrors();
  double yMin = 0.0;
  double yMax = 0.0;
  int outsideIndex = 0;
  for (size_t i = 0; i < reference.size(); ++i) {
    const auto& point = reference[i];
    const double y = 100.0 * point.lab / labMax;
    const double error = 100.0 * point.uncertainty / labMax;
    labGraph->SetPoint(i, point.pointIndex, y);
    labGraph->SetPointError(i, 0.0, error);
    if (!point.inside) {
      outsideLab->SetPoint(outsideIndex, point.pointIndex, y);
      outsideLab->SetPointError(outsideIndex, 0.0, error);
      ++outsideIndex;
    }
    yMin = std::min(yMin, y - error);
    yMax = std::max(yMax, y + error);
  }

  const Color_t colors[] = {kRed + 1, kBlue + 1, kMagenta + 1, kGreen + 2};
  const Style_t markers[] = {24, 25, 26, 32};
  std::vector<TGraph*> simulations;
  for (size_t surfaceIndex = 0; surfaceIndex < kSurfaceOrder.size(); ++surfaceIndex) {
    auto* graph = new TGraph();
    const auto& points = bySurface[kSurfaceOrder[surfaceIndex]];
    for (const auto& point : points) {
      const double y = 100.0 * point.scaledSim / labMax;
      graph->SetPoint(graph->GetN(), point.pointIndex, y);
      yMax = std::max(yMax, y);
    }
    graph->SetLineColor(colors[surfaceIndex]);
    graph->SetMarkerColor(colors[surfaceIndex]);
    graph->SetMarkerStyle(markers[surfaceIndex]);
    graph->SetMarkerSize(0.72);
    graph->SetLineWidth(2);
    simulations.push_back(graph);
  }

  ConfigurePad();
  const double span = yMax - yMin;
  const double pad = span > 0.0 ? 0.12 * span : 10.0;
  auto* frame = gPad->DrawFrame(
    0.5, yMin - pad, reference.size() + 0.5, yMax + pad,
    Form("%s, %g mrad;lab scan point index;response / lab max [%%]",
         reference.front().sampleTitle.c_str(),
         reference.front().divergence));
  frame->GetYaxis()->SetTitleOffset(0.90);
  labGraph->SetMarkerStyle(20);
  labGraph->SetMarkerSize(0.78);
  labGraph->SetMarkerColor(kBlack);
  labGraph->SetLineColor(kBlack);
  labGraph->Draw("P same");
  outsideLab->SetMarkerStyle(21);
  outsideLab->SetMarkerColor(kCyan + 2);
  outsideLab->SetLineColor(kCyan + 2);
  outsideLab->Draw("P same");
  for (auto* graph : simulations) graph->Draw("LP same");

  auto* legend = new TLegend(0.73, 0.55, 0.985, 0.91);
  legend->SetBorderSize(0);
  legend->SetFillColorAlpha(kWhite, 0.90);
  legend->SetTextSize(0.023);
  legend->AddEntry(labGraph, "lab", "pe");
  for (size_t i = 0; i < simulations.size(); ++i) {
    legend->AddEntry(simulations[i], ShortSurfaceLabel(kSurfaceOrder[i]).c_str(), "lp");
  }
  legend->Draw();
}

}  // namespace

void plot_lab_v2_surface(
  const TString& analysisDir = "test/OpNovice2/lab_run_v2/surface_analysis",
  double divergenceMrad = 75.0)
{
  if (!std::isfinite(divergenceMrad) || divergenceMrad < 0.0) {
    throw std::runtime_error("divergenceMrad must be finite and non-negative");
  }
  gStyle->SetOptStat(0);
  gStyle->SetTextFont(42);
  gStyle->SetLabelFont(42, "XYZ");
  gStyle->SetTitleFont(42, "XYZ");
  gStyle->SetCanvasColor(kWhite);
  gStyle->SetPadColor(kWhite);
  gSystem->mkdir(analysisDir, true);

  TString divergenceToken = Form("%g", divergenceMrad);
  divergenceToken.ReplaceAll(".", "p");
  const TString divergenceTag = divergenceToken + "mrad";
  const auto profiles = ReadSurfaceProfiles(
    JoinSurfacePath(analysisDir, Form("surface_profiles_%s.csv", divergenceTag.Data())));
  const auto globalMetrics = ReadSurfaceGlobalMetrics(
    JoinSurfacePath(analysisDir, Form("surface_global_metrics_%s.csv", divergenceTag.Data())));
  const auto sampleMetrics = ReadSurfaceSampleMetrics(
    JoinSurfacePath(analysisDir, Form("surface_sample_metrics_%s.csv", divergenceTag.Data())));

  for (const auto& point : profiles) {
    if (!std::isfinite(point.divergence) || std::abs(point.divergence - divergenceMrad) > 1e-9) {
      throw std::runtime_error(
        Form("Profile divergence does not match requested %g mrad", divergenceMrad));
    }
  }
  const auto sampleOrder = ActiveSamples(profiles);
  if (sampleOrder.empty()) {
    throw std::runtime_error("No recognized lab v2 samples found in surface profiles");
  }

  for (const auto& surface : kSurfaceOrder) {
    auto* canvas = new TCanvas(
      Form("c_surface_%s", surface.c_str()),
      Form("Lab v2 %s at %g mrad", surface.c_str(), divergenceMrad),
      1500,
      sampleOrder.size() <= 2 ? 650 : 1050);
    canvas->SetFillColor(kWhite);
    DivideForSamples(canvas, sampleOrder.size());
    for (size_t sampleIndex = 0; sampleIndex < sampleOrder.size(); ++sampleIndex) {
      std::vector<SurfaceProfilePoint> points;
      for (const auto& point : profiles) {
        if (point.surface == surface && point.sampleId == sampleOrder[sampleIndex]) {
          points.push_back(point);
        }
      }
      if (points.empty()) {
        throw std::runtime_error(
          "Missing profile rows for " + surface + ", " + sampleOrder[sampleIndex]);
      }
      std::sort(points.begin(), points.end(), [](const auto& a, const auto& b) {
        return a.pointIndex < b.pointIndex;
      });
      canvas->cd(static_cast<int>(sampleIndex + 1));
      DrawSurfaceQuadrantPanel(points);
    }
    canvas->cd(0);
    canvas->Modified();
    canvas->Update();
    canvas->SaveAs(JoinSurfacePath(
      analysisDir,
      Form("lab_v2_surface_%s_%s.png", surface.c_str(), divergenceTag.Data())));
    canvas->SaveAs(JoinSurfacePath(
      analysisDir,
      Form("lab_v2_surface_%s_%s.pdf", surface.c_str(), divergenceTag.Data())));
    delete canvas;
  }

  auto* overview = new TCanvas(
    "c_surface_overview", Form("Lab v2 surface overview at %g mrad", divergenceMrad), 1500, 1050);
  overview->SetFillColor(kWhite);
  overview->Divide(2, 2, 0.002, 0.002);
  overview->cd(1);
  DrawSurfaceBars(
    globalMetrics,
    Form("Primary: equal-weight per-tile residual (%g mrad)", divergenceMrad),
    "mean scaled normalized RMSE [%]",
    &SurfaceGlobalMetric::meanSampleScaledRmse,
    100.0);
  overview->cd(2);
  DrawSurfaceBars(
    globalMetrics,
    Form("Equal-weight per-tile uncertainty diagnostic (%g mrad)", divergenceMrad),
    "mean #chi^{2}/ndf",
    &SurfaceGlobalMetric::meanSampleChi2Ndf,
    1.0);
  overview->cd(3);
  DrawSurfaceBars(
    globalMetrics,
    Form("One shared scale across selected tiles (%g mrad)", divergenceMrad),
    "global #chi^{2}/ndf",
    &SurfaceGlobalMetric::globalChi2Ndf,
    1.0);
  overview->cd(4);
  DrawPerSampleSurfaceScaledRmse(sampleMetrics, sampleOrder, divergenceMrad);
  overview->Modified();
  overview->Update();
  overview->SaveAs(JoinSurfacePath(
    analysisDir, Form("lab_v2_surface_overview_%s.png", divergenceTag.Data())));
  overview->SaveAs(JoinSurfacePath(
    analysisDir, Form("lab_v2_surface_overview_%s.pdf", divergenceTag.Data())));
  delete overview;

  auto* overlay = new TCanvas(
    "c_surface_overlay",
    Form("Lab v2 surfaces by tile at %g mrad", divergenceMrad),
    1500,
    sampleOrder.size() <= 2 ? 650 : 1050);
  overlay->SetFillColor(kWhite);
  DivideForSamples(overlay, sampleOrder.size());
  for (size_t i = 0; i < sampleOrder.size(); ++i) {
    overlay->cd(static_cast<int>(i + 1));
    DrawCrossSurfacePanel(profiles, sampleOrder[i]);
  }
  overlay->Modified();
  overlay->Update();
  overlay->SaveAs(JoinSurfacePath(
    analysisDir, Form("lab_v2_surface_overlay_by_tile_%s.png", divergenceTag.Data())));
  overlay->SaveAs(JoinSurfacePath(
    analysisDir, Form("lab_v2_surface_overlay_by_tile_%s.pdf", divergenceTag.Data())));
  delete overlay;
}

#endif
