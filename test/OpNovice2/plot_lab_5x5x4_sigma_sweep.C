#ifndef OPNOVICE2_PLOT_LAB_5X5X4_SIGMA_SWEEP_C
#define OPNOVICE2_PLOT_LAB_5X5X4_SIGMA_SWEEP_C

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

#include <fstream>
#include <map>

namespace {

struct SigmaSummary {
  TString scanLabel;
  double sigmaMm = kNaN;
  int jobId = 0;
  double allRmse = kNaN;
  double insideRmse = kNaN;
  double outsideRmse = kNaN;
  double allPearson = kNaN;
  double insidePearson = kNaN;
  double outsidePearson = kNaN;
};

struct SigmaProfilePoint {
  TString scanLabel;
  double sigmaMm = kNaN;
  int pointIndex = 0;
  double xMm = kNaN;
  double yMm = kNaN;
  bool inside = false;
  double labResponse = kNaN;
  double labUncertainty = kNaN;
  double scaledSimAll = kNaN;
};

TString JoinPath(TString base, const TString& child)
{
  if (base.EndsWith("/")) base.Chop();
  if (base.IsNull() || base == ".") return child;
  return Form("%s/%s", base.Data(), child.Data());
}

bool ParseBool(const std::string& text)
{
  const std::string value = Trim(text);
  return value == "True" || value == "true" || value == "1";
}

std::map<std::string, int> HeaderColumns(const std::vector<std::string>& header)
{
  std::map<std::string, int> columns;
  for (size_t i = 0; i < header.size(); ++i) {
    columns[Trim(header[i])] = static_cast<int>(i);
  }
  return columns;
}

std::vector<SigmaSummary> ReadSigmaSummary(const TString& path)
{
  std::ifstream in(path.Data());
  if (!in) {
    throw std::runtime_error(std::string("Cannot open sigma summary CSV: ") + path.Data());
  }

  std::string headerLine;
  if (!std::getline(in, headerLine)) {
    throw std::runtime_error(std::string("Empty sigma summary CSV: ") + path.Data());
  }

  const auto columns = HeaderColumns(SplitCsvLine(headerLine));
  const int labelCol = ColumnIndex(columns, "scan_label");
  const int sigmaCol = ColumnIndex(columns, "beam_sigma_mm");
  const int jobCol = ColumnIndex(columns, "jobid");
  const int allRmseCol = ColumnIndex(columns, "all_normalized_rmse");
  const int insideRmseCol = ColumnIndex(columns, "inside_normalized_rmse");
  const int outsideRmseCol = ColumnIndex(columns, "outside_normalized_rmse");
  const int allPearsonCol = ColumnIndex(columns, "all_pearson_r");
  const int insidePearsonCol = ColumnIndex(columns, "inside_pearson_r");
  const int outsidePearsonCol = ColumnIndex(columns, "outside_pearson_r");

  std::vector<SigmaSummary> rows;
  std::string line;
  while (std::getline(in, line)) {
    if (Trim(line).empty()) continue;
    auto fields = SplitCsvLine(line);
    if (fields.size() < columns.size()) fields.resize(columns.size());

    SigmaSummary row;
    row.scanLabel = fields[labelCol];
    row.sigmaMm = ParseDouble(fields[sigmaCol]);
    row.jobId = static_cast<int>(ParseDouble(fields[jobCol]));
    row.allRmse = ParseDouble(fields[allRmseCol]);
    row.insideRmse = ParseDouble(fields[insideRmseCol]);
    row.outsideRmse = ParseDouble(fields[outsideRmseCol]);
    row.allPearson = ParseDouble(fields[allPearsonCol]);
    row.insidePearson = ParseDouble(fields[insidePearsonCol]);
    row.outsidePearson = ParseDouble(fields[outsidePearsonCol]);
    rows.push_back(row);
  }
  std::sort(rows.begin(), rows.end(), [](const SigmaSummary& a, const SigmaSummary& b) {
    return a.sigmaMm < b.sigmaMm;
  });
  return rows;
}

std::vector<SigmaProfilePoint> ReadSigmaProfiles(const TString& path)
{
  std::ifstream in(path.Data());
  if (!in) {
    throw std::runtime_error(std::string("Cannot open sigma profile CSV: ") + path.Data());
  }

  std::string headerLine;
  if (!std::getline(in, headerLine)) {
    throw std::runtime_error(std::string("Empty sigma profile CSV: ") + path.Data());
  }

  const auto columns = HeaderColumns(SplitCsvLine(headerLine));
  const int labelCol = ColumnIndex(columns, "scan_label");
  const int sigmaCol = ColumnIndex(columns, "beam_sigma_mm");
  const int pointCol = ColumnIndex(columns, "point_index");
  const int xCol = ColumnIndex(columns, "x");
  const int yCol = ColumnIndex(columns, "y");
  const int insideCol = ColumnIndex(columns, "inside");
  const int labCol = ColumnIndex(columns, "lab_response");
  const int uncCol = ColumnIndex(columns, "uncertainty");
  const int simCol = ColumnIndex(columns, "scaled_sim_all");

  std::vector<SigmaProfilePoint> rows;
  std::string line;
  while (std::getline(in, line)) {
    if (Trim(line).empty()) continue;
    auto fields = SplitCsvLine(line);
    if (fields.size() < columns.size()) fields.resize(columns.size());

    SigmaProfilePoint row;
    row.scanLabel = fields[labelCol];
    row.sigmaMm = ParseDouble(fields[sigmaCol]);
    row.pointIndex = static_cast<int>(ParseDouble(fields[pointCol]));
    row.xMm = ParseDouble(fields[xCol]);
    row.yMm = ParseDouble(fields[yCol]);
    row.inside = ParseBool(fields[insideCol]);
    row.labResponse = ParseDouble(fields[labCol]);
    row.labUncertainty = ParseDouble(fields[uncCol]);
    row.scaledSimAll = ParseDouble(fields[simCol]);
    rows.push_back(row);
  }
  std::sort(rows.begin(), rows.end(), [](const SigmaProfilePoint& a, const SigmaProfilePoint& b) {
    if (a.sigmaMm != b.sigmaMm) return a.sigmaMm < b.sigmaMm;
    return a.pointIndex < b.pointIndex;
  });
  return rows;
}

const SigmaSummary* FindSummary(const std::vector<SigmaSummary>& summaries, double sigmaMm)
{
  for (const auto& summary : summaries) {
    if (std::abs(summary.sigmaMm - sigmaMm) < 1e-6) return &summary;
  }
  return nullptr;
}

std::map<double, std::vector<SigmaProfilePoint>> GroupProfilesBySigma(
  const std::vector<SigmaProfilePoint>& points)
{
  std::map<double, std::vector<SigmaProfilePoint>> grouped;
  for (const auto& point : points) grouped[point.sigmaMm].push_back(point);
  return grouped;
}

TGraph* MakeMetricGraph(const std::vector<SigmaSummary>& rows,
                        double SigmaSummary::*field,
                        Color_t color,
                        Style_t marker,
                        bool asPercent)
{
  auto* graph = new TGraph();
  int n = 0;
  for (const auto& row : rows) {
    const double value = row.*field;
    if (!std::isfinite(row.sigmaMm) || !std::isfinite(value)) continue;
    graph->SetPoint(n++, row.sigmaMm, asPercent ? 100.0 * value : value);
  }
  graph->SetLineColor(color);
  graph->SetMarkerColor(color);
  graph->SetMarkerStyle(marker);
  graph->SetMarkerSize(1.1);
  graph->SetLineWidth(2);
  return graph;
}

void DrawMetricPanel(const std::vector<SigmaSummary>& summaries,
                     const char* title,
                     bool pearsonPanel)
{
  double xMin = std::numeric_limits<double>::infinity();
  double xMax = -std::numeric_limits<double>::infinity();
  double yMax = 0.0;
  double yMin = pearsonPanel ? 100.0 : 0.0;
  for (const auto& row : summaries) {
    xMin = std::min(xMin, row.sigmaMm);
    xMax = std::max(xMax, row.sigmaMm);
    const double values[] = {
      pearsonPanel ? row.insidePearson * 100.0 : row.insideRmse * 100.0,
      pearsonPanel ? row.allPearson * 100.0 : row.allRmse * 100.0,
      pearsonPanel ? row.outsidePearson * 100.0 : row.outsideRmse * 100.0,
    };
    for (double value : values) {
      if (!std::isfinite(value)) continue;
      yMin = std::min(yMin, value);
      yMax = std::max(yMax, value);
    }
  }
  if (!std::isfinite(xMin) || !std::isfinite(xMax)) {
    xMin = 0.0;
    xMax = 20.0;
  }
  const double xPad = std::max(1.0, 0.06 * (xMax - xMin));
  const double yPad = std::max(2.0, 0.12 * (yMax - yMin));
  if (pearsonPanel) {
    yMin = std::max(0.0, yMin - yPad);
    yMax = std::min(105.0, yMax + yPad);
  }
  else {
    yMin = 0.0;
    yMax += yPad;
  }

  gPad->SetLeftMargin(0.10);
  gPad->SetRightMargin(0.04);
  gPad->SetBottomMargin(0.13);
  gPad->SetTopMargin(0.10);
  gPad->SetGrid(1, 1);

  auto* frame = gPad->DrawFrame(
    xMin - xPad,
    yMin,
    xMax + xPad,
    yMax,
    Form("%s;beam #sigma [mm];%s", title, pearsonPanel ? "Pearson r [%]" : "normalized RMSE [%]"));
  frame->GetYaxis()->SetTitleOffset(0.85);

  auto* inside = MakeMetricGraph(
    summaries,
    pearsonPanel ? &SigmaSummary::insidePearson : &SigmaSummary::insideRmse,
    kBlue + 1,
    20,
    true);
  auto* all = MakeMetricGraph(
    summaries,
    pearsonPanel ? &SigmaSummary::allPearson : &SigmaSummary::allRmse,
    kRed + 1,
    21,
    true);
  auto* outside = MakeMetricGraph(
    summaries,
    pearsonPanel ? &SigmaSummary::outsidePearson : &SigmaSummary::outsideRmse,
    kGreen + 2,
    22,
    true);

  all->Draw("LP same");
  inside->Draw("LP same");
  outside->Draw("LP same");

  auto* legend = new TLegend(0.62, 0.70, 0.92, 0.88);
  legend->SetBorderSize(0);
  legend->SetFillStyle(0);
  legend->SetTextSize(0.036);
  legend->AddEntry(all, "all points", "lp");
  legend->AddEntry(inside, "inside tile", "lp");
  legend->AddEntry(outside, "outside tile", "lp");
  legend->Draw();
}

double MaxLabAbs(const std::vector<SigmaProfilePoint>& points)
{
  double maxValue = 0.0;
  for (const auto& point : points) {
    if (std::isfinite(point.labResponse)) {
      maxValue = std::max(maxValue, std::abs(point.labResponse));
    }
  }
  return maxValue;
}

void DrawProfilePanel(double sigmaMm,
                      const std::vector<SigmaProfilePoint>& points,
                      const SigmaSummary* summary)
{
  const double labMax = MaxLabAbs(points);
  auto* labGraph = new TGraphErrors();
  auto* simGraph = new TGraph();

  double yMin = std::numeric_limits<double>::infinity();
  double yMax = -std::numeric_limits<double>::infinity();
  int n = 0;
  for (const auto& point : points) {
    if (!std::isfinite(point.labResponse) || !std::isfinite(point.scaledSimAll) || labMax == 0.0) {
      continue;
    }
    const double x = point.pointIndex;
    const double labY = 100.0 * point.labResponse / labMax;
    const double labErr = std::isfinite(point.labUncertainty) ? 100.0 * point.labUncertainty / labMax : 0.0;
    const double simY = 100.0 * point.scaledSimAll / labMax;
    labGraph->SetPoint(n, x, labY);
    labGraph->SetPointError(n, 0.0, labErr);
    simGraph->SetPoint(n, x, simY);
    yMin = std::min(yMin, std::min(labY - labErr, simY));
    yMax = std::max(yMax, std::max(labY + labErr, simY));
    ++n;
  }

  if (!std::isfinite(yMin) || !std::isfinite(yMax)) {
    yMin = 0.0;
    yMax = 100.0;
  }
  const double span = yMax - yMin;
  const double pad = span > 0.0 ? 0.15 * span : 10.0;
  yMin = std::min(0.0, yMin - pad);
  yMax += pad;

  gPad->SetLeftMargin(0.13);
  gPad->SetRightMargin(0.04);
  gPad->SetBottomMargin(0.14);
  gPad->SetTopMargin(0.13);
  gPad->SetGrid(1, 1);

  auto* frame = gPad->DrawFrame(
    0.5,
    yMin,
    points.size() + 0.5,
    yMax,
    Form("#sigma = %.0f mm;lab scan point index;response / lab max [%%]", sigmaMm));
  frame->GetXaxis()->SetNdivisions(static_cast<int>(points.size()));
  frame->GetYaxis()->SetTitleOffset(0.88);

  labGraph->SetMarkerStyle(20);
  labGraph->SetMarkerSize(0.85);
  labGraph->SetMarkerColor(kBlack);
  labGraph->SetLineColor(kBlack);
  labGraph->Draw("P same");

  simGraph->SetMarkerStyle(24);
  simGraph->SetMarkerSize(0.90);
  simGraph->SetMarkerColor(kRed + 1);
  simGraph->SetLineColor(kRed + 1);
  simGraph->SetLineWidth(2);
  simGraph->Draw("LP same");

  auto* legend = new TLegend(0.50, 0.75, 0.91, 0.88);
  legend->SetBorderSize(0);
  legend->SetFillStyle(0);
  legend->SetTextSize(0.032);
  legend->AddEntry(labGraph, "lab data", "pe");
  legend->AddEntry(simGraph, "simulation", "lp");
  legend->Draw();

  auto* note = new TPaveText(0.15, 0.75, 0.49, 0.88, "NDC");
  note->SetFillStyle(0);
  note->SetBorderSize(0);
  note->SetTextAlign(12);
  note->SetTextSize(0.030);
  if (summary) {
    note->AddText(Form("all RMSE %.1f%%, r %.3f", 100.0 * summary->allRmse, summary->allPearson));
    note->AddText(Form("inside RMSE %.1f%%, r %.3f", 100.0 * summary->insideRmse, summary->insidePearson));
  }
  note->Draw();
}

}  // namespace

// Usage from the repository root after running:
//   python3 outputs/lab_match_analysis/analyze_lab_5x5x4_sigma_sweep.py
//   root -b -q 'test/OpNovice2/plot_lab_5x5x4_sigma_sweep.C("outputs/lab_match_analysis","test/OpNovice2/lab_run/root_plots")'
void plot_lab_5x5x4_sigma_sweep(const char* analysisDirArg = "outputs/lab_match_analysis",
                                const char* outDirArg = "test/OpNovice2/lab_run/root_plots")
{
  gStyle->SetOptStat(0);
  gStyle->SetPalette(kBird);

  const TString analysisDir(analysisDirArg ? analysisDirArg : "outputs/lab_match_analysis");
  const TString outDir(outDirArg ? outDirArg : "test/OpNovice2/lab_run/root_plots");
  gSystem->mkdir(outDir.Data(), true);

  const auto summaries = ReadSigmaSummary(JoinPath(analysisDir, "lab_5x5x4_sigma_sweep_summary.csv"));
  const auto profiles = ReadSigmaProfiles(JoinPath(analysisDir, "lab_5x5x4_sigma_sweep_profiles.csv"));

  std::cout << "5x5x4 beam sigma sweep, fixed 55 mrad" << std::endl;
  for (const auto& summary : summaries) {
    std::cout << "sigma=" << summary.sigmaMm
              << " mm, job=" << summary.jobId
              << ", all RMSE=" << 100.0 * summary.allRmse << "%"
              << ", all r=" << summary.allPearson
              << ", inside RMSE=" << 100.0 * summary.insideRmse << "%"
              << ", inside r=" << summary.insidePearson
              << std::endl;
  }

  auto* metricsCanvas = new TCanvas(
    "c_lab_5x5x4_sigma_metrics",
    "5x5x4 beam sigma sweep metrics",
    1600,
    900);
  metricsCanvas->Divide(2, 1, 0.01, 0.01);
  metricsCanvas->cd(1);
  DrawMetricPanel(summaries, "5x5x4 fixed 55 mrad: shape error", false);
  metricsCanvas->cd(2);
  DrawMetricPanel(summaries, "5x5x4 fixed 55 mrad: correlation", true);
  SaveCanvas(metricsCanvas, outDir, "lab_5x5x4_sigma_sweep_metrics");

  const auto grouped = GroupProfilesBySigma(profiles);
  auto* profileCanvas = new TCanvas(
    "c_lab_5x5x4_sigma_profiles",
    "5x5x4 beam sigma sweep profiles",
    1800,
    1200);
  profileCanvas->Divide(3, 2, 0.01, 0.01);
  int pad = 1;
  for (const auto& [sigmaMm, points] : grouped) {
    profileCanvas->cd(pad++);
    DrawProfilePanel(sigmaMm, points, FindSummary(summaries, sigmaMm));
  }
  SaveCanvas(profileCanvas, outDir, "lab_5x5x4_sigma_sweep_profiles");
}

#endif  // OPNOVICE2_PLOT_LAB_5X5X4_SIGMA_SWEEP_C
