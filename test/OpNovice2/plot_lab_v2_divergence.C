#ifndef OPNOVICE2_PLOT_LAB_V2_DIVERGENCE_C
#define OPNOVICE2_PLOT_LAB_V2_DIVERGENCE_C

#include "plot_efficiency_map.C"

#include <TAxis.h>
#include <TCanvas.h>
#include <TGraph.h>
#include <TGraphErrors.h>
#include <TLegend.h>
#include <TPaveText.h>
#include <TROOT.h>
#include <TStyle.h>
#include <TSystem.h>

#include <algorithm>
#include <cmath>
#include <fstream>
#include <limits>
#include <map>
#include <set>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

struct ProfilePoint {
  std::string sampleId;
  std::string sampleTitle;
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

struct GlobalMetric {
  double divergence = kNaN;
  std::string region;
  double globalChi2Ndf = kNaN;
  double meanSampleChi2Ndf = kNaN;
  double meanSampleRmse = kNaN;
  double meanSampleScaledRmse = kNaN;
};

struct SampleMetric {
  std::string sampleId;
  double divergence = kNaN;
  std::string region;
  double chi2Ndf = kNaN;
  double rmse = kNaN;
  double scaledRmse = kNaN;
};

std::map<std::string, int> LabV2Header(const std::string& line)
{
  std::map<std::string, int> columns;
  const auto fields = SplitCsvLine(line);
  for (size_t i = 0; i < fields.size(); ++i) {
    columns[Trim(fields[i])] = static_cast<int>(i);
  }
  return columns;
}

int OptionalLabV2Column(const std::map<std::string, int>& columns,
                        const std::string& name)
{
  const auto found = columns.find(name);
  return found == columns.end() ? -1 : found->second;
}

std::vector<ProfilePoint> ReadProfiles(const TString& path)
{
  std::ifstream in(path.Data());
  if (!in) throw std::runtime_error(std::string("Cannot open profile CSV: ") + path.Data());
  std::string line;
  if (!std::getline(in, line)) throw std::runtime_error("Empty profile CSV");
  const auto columns = LabV2Header(line);
  const int sampleCol = ColumnIndex(columns, "sample_id");
  const int titleCol = ColumnIndex(columns, "sample_title");
  const int divergenceCol = ColumnIndex(columns, "divergence_mrad");
  const int indexCol = ColumnIndex(columns, "point_index");
  const int insideCol = ColumnIndex(columns, "inside_tile");
  const int labCol = ColumnIndex(columns, "lab_response");
  const int uncertaintyCol = ColumnIndex(columns, "lab_uncertainty");
  const int simCol = ColumnIndex(columns, "scaled_sim_sample");
  const int allChi2Col = ColumnIndex(columns, "all_chi2_ndf");
  const int allRmseCol = ColumnIndex(columns, "all_normalized_rmse");
  const int allScaledRmseCol = OptionalLabV2Column(columns, "all_scaled_normalized_rmse");
  const int insideChi2Col = ColumnIndex(columns, "inside_chi2_ndf");
  const int insideRmseCol = ColumnIndex(columns, "inside_normalized_rmse");
  const int insideScaledRmseCol = OptionalLabV2Column(columns, "inside_scaled_normalized_rmse");

  std::vector<ProfilePoint> rows;
  while (std::getline(in, line)) {
    if (Trim(line).empty()) continue;
    auto fields = SplitCsvLine(line);
    if (fields.size() < columns.size()) fields.resize(columns.size());
    ProfilePoint row;
    row.sampleId = Trim(fields[sampleCol]);
    row.sampleTitle = Trim(fields[titleCol]);
    row.divergence = ParseDouble(fields[divergenceCol]);
    row.pointIndex = static_cast<int>(ParseDouble(fields[indexCol]));
    row.inside = ParseDouble(fields[insideCol]) != 0.0;
    row.lab = ParseDouble(fields[labCol]);
    row.uncertainty = ParseDouble(fields[uncertaintyCol]);
    row.scaledSim = ParseDouble(fields[simCol]);
    row.allChi2Ndf = ParseDouble(fields[allChi2Col]);
    row.allRmse = ParseDouble(fields[allRmseCol]);
    row.allScaledRmse = allScaledRmseCol >= 0
      ? ParseDouble(fields[allScaledRmseCol])
      : row.allRmse;
    row.insideChi2Ndf = ParseDouble(fields[insideChi2Col]);
    row.insideRmse = ParseDouble(fields[insideRmseCol]);
    row.insideScaledRmse = insideScaledRmseCol >= 0
      ? ParseDouble(fields[insideScaledRmseCol])
      : row.insideRmse;
    rows.push_back(row);
  }
  return rows;
}

std::vector<GlobalMetric> ReadGlobalMetrics(const TString& path)
{
  std::ifstream in(path.Data());
  if (!in) throw std::runtime_error(std::string("Cannot open global metrics: ") + path.Data());
  std::string line;
  if (!std::getline(in, line)) throw std::runtime_error("Empty global metrics CSV");
  const auto columns = LabV2Header(line);
  const int divergenceCol = ColumnIndex(columns, "divergence_mrad");
  const int regionCol = ColumnIndex(columns, "region");
  const int globalChi2Col = ColumnIndex(columns, "global_chi2_ndf");
  const int sampleChi2Col = ColumnIndex(columns, "mean_sample_chi2_ndf");
  const int sampleRmseCol = ColumnIndex(columns, "mean_sample_normalized_rmse");
  const int sampleScaledRmseCol = OptionalLabV2Column(
    columns, "mean_sample_scaled_normalized_rmse");

  std::vector<GlobalMetric> rows;
  while (std::getline(in, line)) {
    if (Trim(line).empty()) continue;
    auto fields = SplitCsvLine(line);
    if (fields.size() < columns.size()) fields.resize(columns.size());
    GlobalMetric row;
    row.divergence = ParseDouble(fields[divergenceCol]);
    row.region = Trim(fields[regionCol]);
    row.globalChi2Ndf = ParseDouble(fields[globalChi2Col]);
    row.meanSampleChi2Ndf = ParseDouble(fields[sampleChi2Col]);
    row.meanSampleRmse = ParseDouble(fields[sampleRmseCol]);
    row.meanSampleScaledRmse = sampleScaledRmseCol >= 0
      ? ParseDouble(fields[sampleScaledRmseCol])
      : row.meanSampleRmse;
    rows.push_back(row);
  }
  return rows;
}

std::vector<SampleMetric> ReadSampleMetrics(const TString& path)
{
  std::ifstream in(path.Data());
  if (!in) throw std::runtime_error(std::string("Cannot open sample metrics: ") + path.Data());
  std::string line;
  if (!std::getline(in, line)) throw std::runtime_error("Empty sample metrics CSV");
  const auto columns = LabV2Header(line);
  const int sampleCol = ColumnIndex(columns, "sample_id");
  const int divergenceCol = ColumnIndex(columns, "divergence_mrad");
  const int regionCol = ColumnIndex(columns, "region");
  const int chi2Col = ColumnIndex(columns, "chi2_ndf");
  const int rmseCol = ColumnIndex(columns, "normalized_rmse");
  const int scaledRmseCol = OptionalLabV2Column(columns, "scaled_normalized_rmse");

  std::vector<SampleMetric> rows;
  while (std::getline(in, line)) {
    if (Trim(line).empty()) continue;
    auto fields = SplitCsvLine(line);
    if (fields.size() < columns.size()) fields.resize(columns.size());
    SampleMetric row;
    row.sampleId = Trim(fields[sampleCol]);
    row.divergence = ParseDouble(fields[divergenceCol]);
    row.region = Trim(fields[regionCol]);
    row.chi2Ndf = ParseDouble(fields[chi2Col]);
    row.rmse = ParseDouble(fields[rmseCol]);
    row.scaledRmse = scaledRmseCol >= 0
      ? ParseDouble(fields[scaledRmseCol])
      : row.rmse;
    rows.push_back(row);
  }
  return rows;
}

TString JoinLabV2Path(TString base, const char* child)
{
  if (base.EndsWith("/")) base.Chop();
  return Form("%s/%s", base.Data(), child);
}

double MaxLabAbs(const std::vector<ProfilePoint>& points)
{
  double maximum = 0.0;
  for (const auto& point : points) {
    if (std::isfinite(point.lab)) maximum = std::max(maximum, std::abs(point.lab));
  }
  return maximum;
}

void DrawQuadrantPanel(const std::vector<ProfilePoint>& points,
                       const TString& studyLabel)
{
  if (points.empty()) return;
  const double labMax = MaxLabAbs(points);
  auto* insideLab = new TGraphErrors();
  auto* simGraph = new TGraph();
  auto* outsideLab = new TGraphErrors();
  auto* outsideSim = new TGraph();
  double yMin = 0.0;
  double yMax = 0.0;
  int nInside = 0;
  int nOutside = 0;
  int nSim = 0;
  for (const auto& point : points) {
    if (!std::isfinite(point.lab) || !std::isfinite(point.scaledSim) || labMax <= 0.0) continue;
    const double labY = 100.0 * point.lab / labMax;
    const double labError = std::isfinite(point.uncertainty) ? 100.0 * point.uncertainty / labMax : 0.0;
    const double simY = 100.0 * point.scaledSim / labMax;
    simGraph->SetPoint(nSim++, point.pointIndex, simY);
    if (point.inside) {
      insideLab->SetPoint(nInside, point.pointIndex, labY);
      insideLab->SetPointError(nInside, 0.0, labError);
      ++nInside;
    } else {
      outsideLab->SetPoint(nOutside, point.pointIndex, labY);
      outsideLab->SetPointError(nOutside, 0.0, labError);
      outsideSim->SetPoint(nOutside, point.pointIndex, simY);
      ++nOutside;
    }
    yMin = std::min(yMin, labY - labError);
    yMax = std::max(yMax, std::max(labY + labError, simY));
  }

  const double ySpan = yMax - yMin;
  const double yPad = ySpan > 0.0 ? 0.12 * ySpan : 10.0;
  gPad->SetLeftMargin(0.13);
  gPad->SetRightMargin(0.28);
  gPad->SetBottomMargin(0.13);
  gPad->SetTopMargin(0.12);
  gPad->SetFillColor(kWhite);
  gPad->SetFillStyle(1001);
  gPad->SetFrameFillColor(kWhite);
  gPad->SetGrid(1, 1);
  auto* frame = gPad->DrawFrame(
    0.5,
    yMin - yPad,
    points.size() + 0.5,
    yMax + yPad,
    Form("%s, %.0f mrad (%s);lab scan point index;response / lab max [%%]",
         points.front().sampleTitle.c_str(),
         points.front().divergence,
         studyLabel.Data()));
  frame->GetYaxis()->SetTitleOffset(0.90);
  frame->GetXaxis()->SetNdivisions(
    points.size() > 20 ? 14 : static_cast<int>(points.size()));

  simGraph->SetMarkerStyle(24);
  simGraph->SetMarkerSize(0.80);
  simGraph->SetMarkerColor(kRed + 1);
  simGraph->SetLineColor(kRed + 1);
  simGraph->SetLineWidth(2);
  simGraph->Draw("LP same");

  outsideSim->SetMarkerStyle(25);
  outsideSim->SetMarkerSize(0.88);
  outsideSim->SetMarkerColor(kRed + 1);
  outsideSim->SetLineColor(kRed + 1);
  outsideSim->Draw("P same");

  insideLab->SetMarkerStyle(20);
  insideLab->SetMarkerSize(0.78);
  insideLab->SetMarkerColor(kBlack);
  insideLab->SetLineColor(kBlack);
  insideLab->SetLineWidth(2);
  insideLab->Draw("P same");

  outsideLab->SetMarkerStyle(21);
  outsideLab->SetMarkerSize(0.90);
  outsideLab->SetMarkerColor(kBlue + 1);
  outsideLab->SetLineColor(kBlue + 1);
  outsideLab->SetLineWidth(2);
  outsideLab->Draw("P same");

  auto* legend = new TLegend(0.73, 0.72, 0.985, 0.90);
  legend->SetBorderSize(0);
  legend->SetFillColorAlpha(kWhite, 0.90);
  legend->SetTextSize(0.025);
  legend->AddEntry(insideLab, "lab (inside)", "pe");
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
  note->AddText(Form("all: scaled RMSE = %.1f%%", 100.0 * first.allScaledRmse));
  note->AddText(Form("inside: #chi^{2}/ndf = %.2f", first.insideChi2Ndf));
  note->AddText(Form("inside: scaled RMSE = %.1f%%", 100.0 * first.insideScaledRmse));
  note->Draw();
}

TGraph* MetricGraph(const std::vector<GlobalMetric>& rows,
                    const std::string& region,
                    double GlobalMetric::*field,
                    Color_t color,
                    Style_t marker)
{
  auto* graph = new TGraph();
  int n = 0;
  for (const auto& row : rows) {
    if (row.region != region) continue;
    const double value = row.*field;
    if (!std::isfinite(row.divergence) || !std::isfinite(value)) continue;
    graph->SetPoint(n++, row.divergence, value);
  }
  graph->SetLineColor(color);
  graph->SetMarkerColor(color);
  graph->SetMarkerStyle(marker);
  graph->SetMarkerSize(1.05);
  graph->SetLineWidth(2);
  return graph;
}

void DrawTwoRegionMetric(const std::vector<GlobalMetric>& rows,
                         const char* title,
                         const char* yTitle,
                         double GlobalMetric::*field,
                         double multiplier,
                         const TString& studyLabel,
                         bool focusRange = false)
{
  double yMin = std::numeric_limits<double>::infinity();
  double yMax = 0.0;
  for (const auto& row : rows) {
    if (row.region != "all" && row.region != "inside") continue;
    const double value = multiplier * (row.*field);
    if (std::isfinite(value)) {
      yMin = std::min(yMin, value);
      yMax = std::max(yMax, value);
    }
  }
  const double span = yMax - yMin;
  const double frameMin = focusRange && std::isfinite(yMin)
    ? std::max(0.0, yMin - (span > 0.0 ? 0.18 * span : 0.1 * yMin))
    : 0.0;
  const double frameMax = focusRange && std::isfinite(yMin)
    ? yMax + (span > 0.0 ? 0.18 * span : 0.1 * yMax)
    : 1.15 * yMax;
  gPad->SetLeftMargin(0.13);
  gPad->SetRightMargin(0.04);
  gPad->SetBottomMargin(0.13);
  gPad->SetTopMargin(0.11);
  gPad->SetFillColor(kWhite);
  gPad->SetFillStyle(1001);
  gPad->SetFrameFillColor(kWhite);
  gPad->SetGrid(1, 1);
  auto* frame = gPad->DrawFrame(
    20.0,
    frameMin,
    80.0,
    frameMax,
    Form("%s (%s);beam divergence [mrad];%s", title, studyLabel.Data(), yTitle));
  frame->GetYaxis()->SetTitleOffset(0.90);

  auto* all = MetricGraph(rows, "all", field, kBlack, 20);
  auto* inside = MetricGraph(rows, "inside", field, kBlue + 1, 24);
  if (multiplier != 1.0) {
    for (int i = 0; i < all->GetN(); ++i) all->GetY()[i] *= multiplier;
    for (int i = 0; i < inside->GetN(); ++i) inside->GetY()[i] *= multiplier;
  }
  all->Draw("LP same");
  inside->Draw("LP same");
  auto* legend = new TLegend(0.67, 0.16, 0.92, 0.31);
  legend->SetBorderSize(0);
  legend->SetFillColorAlpha(kWhite, 0.90);
  legend->SetTextSize(0.034);
  legend->AddEntry(all, "all points", "lp");
  legend->AddEntry(inside, "inside tile", "lp");
  legend->Draw();
}

void DrawPerSampleScaledRmse(const std::vector<SampleMetric>& rows,
                             const TString& studyLabel)
{
  const std::vector<std::string> ids = {
    "11p5x11p5x16", "11p5x11p5x4", "5x5x16", "5x5x4"
  };
  const std::vector<std::string> labels = {
    "11.5x11.5x1.6 cm", "11.5x11.5x0.4 cm", "5x5x1.6 cm", "5x5x0.4 cm"
  };
  const Color_t colors[] = {kBlue + 1, kOrange + 7, kMagenta + 1, kGreen + 2};
  const Style_t markers[] = {20, 21, 22, 23};
  double yMax = 0.0;
  std::vector<TGraph*> graphs;
  std::vector<std::string> graphLabels;
  for (size_t i = 0; i < ids.size(); ++i) {
    auto* graph = new TGraph();
    int n = 0;
    for (const auto& row : rows) {
      if (row.region != "all" || row.sampleId != ids[i] || !std::isfinite(row.scaledRmse)) continue;
      const double value = 100.0 * row.scaledRmse;
      graph->SetPoint(n++, row.divergence, value);
      yMax = std::max(yMax, value);
    }
    graph->SetLineColor(colors[i]);
    graph->SetMarkerColor(colors[i]);
    graph->SetMarkerStyle(markers[i]);
    graph->SetMarkerSize(1.0);
    graph->SetLineWidth(2);
    if (graph->GetN() > 0) {
      graphs.push_back(graph);
      graphLabels.push_back(labels[i]);
    } else {
      delete graph;
    }
  }
  gPad->SetLeftMargin(0.13);
  gPad->SetRightMargin(0.04);
  gPad->SetBottomMargin(0.13);
  gPad->SetTopMargin(0.11);
  gPad->SetFillColor(kWhite);
  gPad->SetFillStyle(1001);
  gPad->SetFrameFillColor(kWhite);
  gPad->SetGrid(1, 1);
  auto* frame = gPad->DrawFrame(
    20.0, 0.0, 80.0, 1.15 * yMax,
    Form("Per-tile scaled discrepancy, all points (%s);beam divergence [mrad];scaled normalized RMSE [%%]",
         studyLabel.Data()));
  frame->GetYaxis()->SetTitleOffset(0.90);
  auto* legend = new TLegend(0.54, 0.15, 0.93, 0.38);
  legend->SetBorderSize(0);
  legend->SetFillColorAlpha(kWhite, 0.90);
  legend->SetTextSize(0.029);
  for (size_t i = 0; i < graphs.size(); ++i) {
    graphs[i]->Draw("LP same");
    legend->AddEntry(graphs[i], graphLabels[i].c_str(), "lp");
  }
  legend->Draw();
}

}  // namespace

void plot_lab_v2_divergence(
  const TString& analysisDir = "test/OpNovice2/lab_run_v2/analysis",
  const TString& studyLabel = "polishedfrontpainted, #sigma=5 mm")
{
  gStyle->SetOptStat(0);
  gStyle->SetTextFont(42);
  gStyle->SetLabelFont(42, "XYZ");
  gStyle->SetTitleFont(42, "XYZ");
  gStyle->SetCanvasColor(kWhite);
  gStyle->SetPadColor(kWhite);
  gStyle->SetEndErrorSize(4);
  gROOT->ForceStyle();
  auto* styleWarmup = new TCanvas("c_lab_v2_style_warmup", "", 8, 8);
  styleWarmup->SetFillColor(kWhite);
  styleWarmup->SetFillStyle(1001);
  styleWarmup->Update();
  delete styleWarmup;
  gSystem->mkdir(analysisDir, true);

  const auto profiles = ReadProfiles(JoinLabV2Path(analysisDir, "profiles.csv"));
  const auto globalMetrics = ReadGlobalMetrics(JoinLabV2Path(analysisDir, "global_metrics.csv"));
  const auto sampleMetrics = ReadSampleMetrics(JoinLabV2Path(analysisDir, "sample_metrics.csv"));
  const std::vector<std::string> preferredSampleOrder = {
    "11p5x11p5x16", "11p5x11p5x4", "5x5x16", "5x5x4"
  };
  std::set<std::string> presentSamples;
  for (const auto& profile : profiles) presentSamples.insert(profile.sampleId);
  std::vector<std::string> sampleOrder;
  for (const auto& sampleId : preferredSampleOrder) {
    if (presentSamples.count(sampleId)) sampleOrder.push_back(sampleId);
  }
  std::set<double> divergenceSet;
  for (const auto& metric : globalMetrics) divergenceSet.insert(metric.divergence);
  const std::vector<double> divergences(divergenceSet.begin(), divergenceSet.end());

  for (double divergence : divergences) {
    auto* canvas = new TCanvas(
      Form("c_lab_v2_%.0f", divergence),
      Form("Lab v2 %.0f mrad", divergence),
      1500,
      sampleOrder.size() <= 2 ? 650 : 1050);
    canvas->SetFillColor(kWhite);
    canvas->SetFillStyle(1001);
    if (sampleOrder.size() <= 2) {
      canvas->Divide(static_cast<int>(sampleOrder.size()), 1, 0.002, 0.002);
    } else {
      canvas->Divide(2, 2, 0.002, 0.002);
    }
    for (size_t i = 0; i < sampleOrder.size(); ++i) {
      std::vector<ProfilePoint> points;
      for (const auto& point : profiles) {
        if (point.sampleId == sampleOrder[i] && std::abs(point.divergence - divergence) < 1e-6) {
          points.push_back(point);
        }
      }
      std::sort(points.begin(), points.end(), [](const ProfilePoint& a, const ProfilePoint& b) {
        return a.pointIndex < b.pointIndex;
      });
      canvas->cd(static_cast<int>(i + 1));
      DrawQuadrantPanel(points, studyLabel);
    }
    canvas->cd(0);
    canvas->SaveAs(JoinLabV2Path(analysisDir, Form("lab_v2_quadrant_%02.0fmrad.png", divergence)));
    canvas->SaveAs(JoinLabV2Path(analysisDir, Form("lab_v2_quadrant_%02.0fmrad.pdf", divergence)));
    delete canvas;
  }

  auto* overview = new TCanvas("c_lab_v2_overview", "Lab v2 divergence overview", 1500, 1050);
  overview->Divide(2, 2, 0.002, 0.002);
  overview->cd(1);
  DrawTwoRegionMetric(
    globalMetrics,
    "Equal-weight mean of per-tile fits",
    "mean #chi^{2}/ndf",
    &GlobalMetric::meanSampleChi2Ndf,
    1.0,
    studyLabel);
  overview->cd(2);
  DrawTwoRegionMetric(
    globalMetrics,
    "One shared scale across selected tiles",
    "global #chi^{2}/ndf",
    &GlobalMetric::globalChi2Ndf,
    1.0,
    studyLabel);
  overview->cd(3);
  DrawTwoRegionMetric(
    globalMetrics,
    "Equal-weight per-tile scaled discrepancy",
    "scaled normalized RMSE [%]",
    &GlobalMetric::meanSampleScaledRmse,
    100.0,
    studyLabel,
    true);
  overview->cd(4);
  DrawPerSampleScaledRmse(sampleMetrics, studyLabel);
  overview->SaveAs(JoinLabV2Path(analysisDir, "lab_v2_divergence_overview.png"));
  overview->SaveAs(JoinLabV2Path(analysisDir, "lab_v2_divergence_overview.pdf"));
  delete overview;
}

#endif
