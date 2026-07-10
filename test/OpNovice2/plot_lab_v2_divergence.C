#ifndef OPNOVICE2_PLOT_LAB_V2_DIVERGENCE_C
#define OPNOVICE2_PLOT_LAB_V2_DIVERGENCE_C

#include "plot_efficiency_map.C"

#include <TAxis.h>
#include <TCanvas.h>
#include <TGraph.h>
#include <TGraphErrors.h>
#include <TLegend.h>
#include <TPaveText.h>
#include <TStyle.h>
#include <TSystem.h>

#include <algorithm>
#include <cmath>
#include <fstream>
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
  double insideChi2Ndf = kNaN;
  double insideRmse = kNaN;
};

struct GlobalMetric {
  double divergence = kNaN;
  std::string region;
  double globalChi2Ndf = kNaN;
  double meanSampleChi2Ndf = kNaN;
  double meanSampleRmse = kNaN;
};

struct SampleMetric {
  std::string sampleId;
  double divergence = kNaN;
  std::string region;
  double chi2Ndf = kNaN;
  double rmse = kNaN;
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
  const int insideChi2Col = ColumnIndex(columns, "inside_chi2_ndf");
  const int insideRmseCol = ColumnIndex(columns, "inside_normalized_rmse");

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
    row.insideChi2Ndf = ParseDouble(fields[insideChi2Col]);
    row.insideRmse = ParseDouble(fields[insideRmseCol]);
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

void DrawQuadrantPanel(const std::vector<ProfilePoint>& points)
{
  if (points.empty()) return;
  const double labMax = MaxLabAbs(points);
  auto* labGraph = new TGraphErrors();
  auto* simGraph = new TGraph();
  auto* outsideLab = new TGraphErrors();
  auto* outsideSim = new TGraph();
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
  gPad->SetGrid(1, 1);
  auto* frame = gPad->DrawFrame(
    0.5,
    yMin - yPad,
    points.size() + 0.5,
    yMax + yPad,
    Form("%s, %.0f mrad;lab scan point index;response / lab max [%%]",
         points.front().sampleTitle.c_str(),
         points.front().divergence));
  frame->GetYaxis()->SetTitleOffset(0.90);
  frame->GetXaxis()->SetNdivisions(
    points.size() > 20 ? 14 : static_cast<int>(points.size()));

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
  outsideLab->SetMarkerSize(0.85);
  outsideLab->SetMarkerColor(kBlue + 1);
  outsideLab->SetLineColor(kBlue + 1);
  outsideLab->Draw("P same");
  outsideSim->SetMarkerStyle(25);
  outsideSim->SetMarkerSize(0.88);
  outsideSim->SetMarkerColor(kRed + 1);
  outsideSim->SetLineColor(kRed + 1);
  outsideSim->Draw("P same");

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
  note->AddText(Form("all: normalized RMSE = %.1f%%", 100.0 * first.allRmse));
  note->AddText(Form("inside: #chi^{2}/ndf = %.2f", first.insideChi2Ndf));
  note->AddText(Form("inside: normalized RMSE = %.1f%%", 100.0 * first.insideRmse));
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
                         double multiplier)
{
  double yMax = 0.0;
  for (const auto& row : rows) {
    if (row.region != "all" && row.region != "inside") continue;
    const double value = multiplier * (row.*field);
    if (std::isfinite(value)) yMax = std::max(yMax, value);
  }
  gPad->SetLeftMargin(0.13);
  gPad->SetRightMargin(0.04);
  gPad->SetBottomMargin(0.13);
  gPad->SetTopMargin(0.11);
  gPad->SetGrid(1, 1);
  auto* frame = gPad->DrawFrame(20.0, 0.0, 80.0, 1.15 * yMax, Form("%s;beam divergence [mrad];%s", title, yTitle));
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

void DrawPerSampleChi2(const std::vector<SampleMetric>& rows)
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
  for (size_t i = 0; i < ids.size(); ++i) {
    auto* graph = new TGraph();
    int n = 0;
    for (const auto& row : rows) {
      if (row.region != "all" || row.sampleId != ids[i] || !std::isfinite(row.chi2Ndf)) continue;
      graph->SetPoint(n++, row.divergence, row.chi2Ndf);
      yMax = std::max(yMax, row.chi2Ndf);
    }
    graph->SetLineColor(colors[i]);
    graph->SetMarkerColor(colors[i]);
    graph->SetMarkerStyle(markers[i]);
    graph->SetMarkerSize(1.0);
    graph->SetLineWidth(2);
    graphs.push_back(graph);
  }
  gPad->SetLeftMargin(0.13);
  gPad->SetRightMargin(0.04);
  gPad->SetBottomMargin(0.13);
  gPad->SetTopMargin(0.11);
  gPad->SetGrid(1, 1);
  auto* frame = gPad->DrawFrame(
    20.0, 0.0, 80.0, 1.15 * yMax,
    "Per-sample shape fit, all points;beam divergence [mrad];#chi^{2}/ndf");
  frame->GetYaxis()->SetTitleOffset(0.90);
  auto* legend = new TLegend(0.54, 0.15, 0.93, 0.38);
  legend->SetBorderSize(0);
  legend->SetFillColorAlpha(kWhite, 0.90);
  legend->SetTextSize(0.029);
  for (size_t i = 0; i < graphs.size(); ++i) {
    graphs[i]->Draw("LP same");
    legend->AddEntry(graphs[i], labels[i].c_str(), "lp");
  }
  legend->Draw();
}

}  // namespace

void plot_lab_v2_divergence(
  const TString& analysisDir = "test/OpNovice2/lab_run_v2/analysis")
{
  gStyle->SetOptStat(0);
  gStyle->SetTextFont(42);
  gStyle->SetLabelFont(42, "XYZ");
  gStyle->SetTitleFont(42, "XYZ");
  gStyle->SetCanvasColor(kWhite);
  gStyle->SetPadColor(kWhite);
  gSystem->mkdir(analysisDir, true);

  const auto profiles = ReadProfiles(JoinLabV2Path(analysisDir, "profiles.csv"));
  const auto globalMetrics = ReadGlobalMetrics(JoinLabV2Path(analysisDir, "global_metrics.csv"));
  const auto sampleMetrics = ReadSampleMetrics(JoinLabV2Path(analysisDir, "sample_metrics.csv"));
  const std::vector<std::string> sampleOrder = {
    "11p5x11p5x16", "11p5x11p5x4", "5x5x16", "5x5x4"
  };
  std::set<double> divergenceSet;
  for (const auto& metric : globalMetrics) divergenceSet.insert(metric.divergence);
  const std::vector<double> divergences(divergenceSet.begin(), divergenceSet.end());

  for (double divergence : divergences) {
    auto* canvas = new TCanvas(
      Form("c_lab_v2_%.0f", divergence),
      Form("Lab v2 %.0f mrad", divergence),
      1500,
      1050);
    canvas->Divide(2, 2, 0.002, 0.002);
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
      DrawQuadrantPanel(points);
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
    1.0);
  overview->cd(2);
  DrawTwoRegionMetric(
    globalMetrics,
    "One shared scale across four tiles",
    "global #chi^{2}/ndf",
    &GlobalMetric::globalChi2Ndf,
    1.0);
  overview->cd(3);
  DrawTwoRegionMetric(
    globalMetrics,
    "Mean spatial-shape discrepancy",
    "normalized RMSE [%]",
    &GlobalMetric::meanSampleRmse,
    100.0);
  overview->cd(4);
  DrawPerSampleChi2(sampleMetrics);
  overview->SaveAs(JoinLabV2Path(analysisDir, "lab_v2_divergence_overview.png"));
  overview->SaveAs(JoinLabV2Path(analysisDir, "lab_v2_divergence_overview.pdf"));
  delete overview;
}

#endif
