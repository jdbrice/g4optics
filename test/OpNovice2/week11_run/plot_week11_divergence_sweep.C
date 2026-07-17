#ifndef OPNOVICE2_PLOT_WEEK11_DIVERGENCE_SWEEP_C
#define OPNOVICE2_PLOT_WEEK11_DIVERGENCE_SWEEP_C

#include "../plot_efficiency_map.C"

#include <TGraph.h>
#include <TLegend.h>
#include <TROOT.h>
#include <TSystem.h>

#include <fstream>
#include <map>

namespace {

struct SweepSpec {
  TString label;
  TString dir;
  double divergenceMrad = 0.0;
  double fiducialLimitMm = 45.0;
};

struct SweepRun {
  SweepSpec spec;
  std::vector<EfficiencyPoint> points;
};

struct SweepSummary {
  TString label;
  double divergenceMrad = 0.0;
  TString scope;
  int n = 0;
  double meanEff = kNaN;
  double rmsOverMean = kNaN;
  double detPerEvent = kNaN;
  double scintPerEvent = kNaN;
};

struct MetricDef {
  TString title;
  TString yTitle;
  int index = 0;
};

TString JoinPath(TString base, const TString& child)
{
  if (base.EndsWith("/")) base.Chop();
  return Form("%s/%s", base.Data(), child.Data());
}

SweepSummary BuildSweepSummary(const SweepRun& run, const TString& scope, bool fiducial)
{
  const auto stats = SummarizeRegion(run.points, scope.Data(), run.spec.fiducialLimitMm, fiducial);
  SweepSummary row;
  row.label = run.spec.label;
  row.divergenceMrad = run.spec.divergenceMrad;
  row.scope = scope;
  row.n = stats.nPoints;
  row.meanEff = stats.collectionEfficiency.Mean();
  const double rms = stats.collectionEfficiency.Rms();
  row.rmsOverMean = std::isfinite(row.meanEff) && row.meanEff != 0.0 ? rms / row.meanEff : kNaN;
  row.detPerEvent = stats.detectedPerEvent.Mean();
  row.scintPerEvent = stats.scintillationPerEvent.Mean();
  return row;
}

double MetricValue(const SweepSummary& row, int metric)
{
  if (metric == 0) return row.meanEff * 100.0;
  if (metric == 1) return row.rmsOverMean * 100.0;
  if (metric == 2) return row.detPerEvent;
  return kNaN;
}

void WriteSummaryCsv(const std::vector<SweepSummary>& rows, const TString& outPath)
{
  std::ofstream out(outPath.Data());
  out << "label,divergence_mrad,scope,n_points,mean_eff,rms_over_mean,detected_per_event,"
      << "scintillation_per_event\n";
  out << std::setprecision(12);
  for (const auto& row : rows) {
    out << row.label << ','
        << row.divergenceMrad << ','
        << row.scope << ','
        << row.n << ','
        << row.meanEff << ','
        << row.rmsOverMean << ','
        << row.detPerEvent << ','
        << row.scintPerEvent << '\n';
  }
}

std::vector<SweepSummary> ScopeRows(const std::vector<SweepSummary>& rows, const TString& scope)
{
  std::vector<SweepSummary> filtered;
  for (const auto& row : rows) {
    if (row.scope == scope) filtered.push_back(row);
  }
  return filtered;
}

std::vector<TString> LabelsInOrder(const std::vector<SweepSummary>& rows)
{
  std::vector<TString> labels;
  for (const auto& row : rows) {
    bool seen = false;
    for (const auto& label : labels) {
      if (label == row.label) {
        seen = true;
        break;
      }
    }
    if (!seen) labels.push_back(row.label);
  }
  return labels;
}

std::vector<SweepSummary> RowsForLabel(const std::vector<SweepSummary>& rows, const TString& label)
{
  std::vector<SweepSummary> selected;
  for (const auto& row : rows) {
    if (row.label == label) selected.push_back(row);
  }
  std::sort(selected.begin(), selected.end(), [](const SweepSummary& a, const SweepSummary& b) {
    return a.divergenceMrad < b.divergenceMrad;
  });
  return selected;
}

double Baseline100(const std::vector<SweepSummary>& rows, int metric)
{
  for (const auto& row : rows) {
    if (std::abs(row.divergenceMrad - 100.0) < 1.e-9) return MetricValue(row, metric);
  }
  return kNaN;
}

void RangeForMetric(const std::vector<SweepSummary>& rows,
                    int metric,
                    bool relativeTo100,
                    double& minValue,
                    double& maxValue)
{
  minValue = std::numeric_limits<double>::infinity();
  maxValue = -std::numeric_limits<double>::infinity();
  const auto labels = LabelsInOrder(rows);
  for (const auto& label : labels) {
    const auto selected = RowsForLabel(rows, label);
    const double baseline = relativeTo100 ? Baseline100(selected, metric) : 1.0;
    for (const auto& row : selected) {
      double value = MetricValue(row, metric);
      if (relativeTo100) {
        if (!std::isfinite(baseline) || baseline == 0.0) continue;
        value /= baseline;
      }
      if (!std::isfinite(value)) continue;
      minValue = std::min(minValue, value);
      maxValue = std::max(maxValue, value);
    }
  }
  if (!std::isfinite(minValue) || !std::isfinite(maxValue)) {
    minValue = 0.0;
    maxValue = 1.0;
  }
  const double span = maxValue - minValue;
  const double pad = span > 0.0 ? 0.12 * span : std::max(std::abs(maxValue) * 0.08, 1.e-3);
  minValue -= pad;
  maxValue += pad;
}

void DrawTrendPanel(const std::vector<SweepSummary>& rows,
                    const TString& outDir,
                    const TString& stem,
                    bool relativeTo100,
                    const TString& scopeLabel)
{
  const std::vector<MetricDef> metrics = {
    {"Mean collection efficiency", relativeTo100 ? "ratio to 100 mrad" : "collection efficiency [%]", 0},
    {"Spatial non-uniformity", relativeTo100 ? "ratio to 100 mrad" : "RMS / mean [%]", 1},
    {"Detected light yield", relativeTo100 ? "ratio to 100 mrad" : "detected photons / event", 2},
  };
  const auto labels = LabelsInOrder(rows);
  const int colors[] = {kAzure + 2, kOrange + 7, kGreen + 2, kRed + 1, kViolet + 1, kGray + 2};
  const int markers[] = {20, 21, 22, 23, 29, 33};

  auto* canvas = new TCanvas(Form("c_%s", stem.Data()), stem, 1800, 1200);
  canvas->Divide(1, 3, 0.01, 0.01);
  for (const auto& metric : metrics) {
    canvas->cd(metric.index + 1);
    gPad->SetLeftMargin(0.10);
    gPad->SetRightMargin(0.03);
    gPad->SetBottomMargin(0.13);
    gPad->SetTopMargin(0.10);
    gPad->SetGrid(1, 1);

    double minValue = 0.0;
    double maxValue = 1.0;
    RangeForMetric(rows, metric.index, relativeTo100, minValue, maxValue);
    auto* frame = gPad->DrawFrame(0.0,
                                  minValue,
                                  105.0,
                                  maxValue,
                                  Form("%s: %s;beam divergence #sigma_{x,y} [mrad];%s",
                                       scopeLabel.Data(),
                                       metric.title.Data(),
                                       metric.yTitle.Data()));
    frame->GetXaxis()->SetNdivisions(510);
    frame->GetYaxis()->SetTitleOffset(0.70);

    auto* legend = new TLegend(0.13, 0.66, 0.48, 0.88);
    legend->SetBorderSize(0);
    legend->SetFillStyle(0);
    legend->SetTextSize(0.035);

    for (size_t i = 0; i < labels.size(); ++i) {
      const auto selected = RowsForLabel(rows, labels[i]);
      const double baseline = relativeTo100 ? Baseline100(selected, metric.index) : 1.0;
      auto* graph = new TGraph();
      int point = 0;
      for (const auto& row : selected) {
        double value = MetricValue(row, metric.index);
        if (relativeTo100) {
          if (!std::isfinite(baseline) || baseline == 0.0) continue;
          value /= baseline;
        }
        if (!std::isfinite(value)) continue;
        graph->SetPoint(point++, row.divergenceMrad, value);
      }
      graph->SetLineColor(colors[i % 6]);
      graph->SetMarkerColor(colors[i % 6]);
      graph->SetMarkerStyle(markers[i % 6]);
      graph->SetMarkerSize(1.2);
      graph->SetLineWidth(2);
      graph->Draw("LP same");
      legend->AddEntry(graph, labels[i], "lp");
    }
    legend->Draw();
  }
  SaveCanvas(canvas, outDir, stem);
}

void PrintFiducialCore(const std::vector<SweepSummary>& rows)
{
  std::cout << std::setprecision(8);
  for (const auto& label : LabelsInOrder(rows)) {
    const auto selected = RowsForLabel(rows, label);
    std::cout << label << std::endl;
    for (const auto& row : selected) {
      std::cout << "  " << std::setw(3) << row.divergenceMrad << " mrad: "
                << "mean_eff = " << row.meanEff
                << ", rms/mean = " << row.rmsOverMean
                << ", det/event = " << row.detPerEvent
                << std::endl;
    }
  }
}

}  // namespace

void plot_week11_divergence_sweep(const char* baseDirArg = "week11_run",
                                  const char* outDirArg = "week11_run/root_plots")
{
  gROOT->SetBatch(true);
  gStyle->SetOptStat(0);
  gStyle->SetPalette(kBird);

  TString baseDir(baseDirArg);
  if (baseDir.EndsWith("/")) baseDir.Chop();
  TString outDir(outDirArg);
  if (outDir.EndsWith("/")) outDir.Chop();
  gSystem->mkdir(outDir.Data(), true);

  std::vector<SweepRun> runs = {
    {{"50x50x16 back", JoinPath(baseDir, "week11_nominal_50x50x16_center_back_5mrad"), 5.0, 20.0}, {}},
    {{"50x50x16 back", JoinPath(baseDir, "week11_nominal_50x50x16_center_back_25mrad"), 25.0, 20.0}, {}},
    {{"50x50x16 back", JoinPath(baseDir, "week11_nominal_50x50x16_center_back_50mrad"), 50.0, 20.0}, {}},
    {{"50x50x16 back", JoinPath(baseDir, "week11_nominal_50x50x16_center_back"), 100.0, 20.0}, {}},

    {{"100x100x16 back", JoinPath(baseDir, "week11_nominal_100x100x16_center_back_5mrad"), 5.0, 45.0}, {}},
    {{"100x100x16 back", JoinPath(baseDir, "week11_nominal_100x100x16_center_back_25mrad"), 25.0, 45.0}, {}},
    {{"100x100x16 back", JoinPath(baseDir, "week11_nominal_100x100x16_center_back_50mrad"), 50.0, 45.0}, {}},
    {{"100x100x16 back", JoinPath(baseDir, "week11_nominal_100x100x16_center_back"), 100.0, 45.0}, {}},

    {{"100x100x16 side", JoinPath(baseDir, "week11_nominal_100x100x16_center_side_5mrad"), 5.0, 45.0}, {}},
    {{"100x100x16 side", JoinPath(baseDir, "week11_nominal_100x100x16_center_side_25mrad"), 25.0, 45.0}, {}},
    {{"100x100x16 side", JoinPath(baseDir, "week11_nominal_100x100x16_center_side_50mrad"), 50.0, 45.0}, {}},
    {{"100x100x16 side", JoinPath(baseDir, "week11_nominal_100x100x16_center_side_100mrad"), 100.0, 45.0}, {}},

    {{"100x100x24 back", JoinPath(baseDir, "week11_nominal_100x100x24_center_back_5mrad"), 5.0, 45.0}, {}},
    {{"100x100x24 back", JoinPath(baseDir, "week11_nominal_100x100x24_center_back_25mrad"), 25.0, 45.0}, {}},
    {{"100x100x24 back", JoinPath(baseDir, "week11_nominal_100x100x24_center_back_50mrad"), 50.0, 45.0}, {}},
    {{"100x100x24 back", JoinPath(baseDir, "week11_nominal_100x100x24_center_back"), 100.0, 45.0}, {}},

    {{"100x100x24 side", JoinPath(baseDir, "week11_nominal_100x100x24_center_side_5mrad"), 5.0, 45.0}, {}},
    {{"100x100x24 side", JoinPath(baseDir, "week11_nominal_100x100x24_center_side_25mrad"), 25.0, 45.0}, {}},
    {{"100x100x24 side", JoinPath(baseDir, "week11_nominal_100x100x24_center_side_50mrad"), 50.0, 45.0}, {}},
    {{"100x100x24 side", JoinPath(baseDir, "week11_nominal_100x100x24_center_side"), 100.0, 45.0}, {}},

    {{"100x100x24 dimple", JoinPath(baseDir, "week11_nominal_100x100x24_center_back_dimple_r3mm_5mrad"), 5.0, 45.0}, {}},
    {{"100x100x24 dimple", JoinPath(baseDir, "week11_nominal_100x100x24_center_back_dimple_r3mm_25mrad"), 25.0, 45.0}, {}},
    {{"100x100x24 dimple", JoinPath(baseDir, "week11_nominal_100x100x24_center_back_dimple_r3mm_50mrad"), 50.0, 45.0}, {}},
    {{"100x100x24 dimple", JoinPath(baseDir, "week11_nominal_100x100x24_center_back_dimple_r3mm"), 100.0, 45.0}, {}},
  };

  try {
    for (auto& run : runs) {
      run.points = ReadEfficiencyMap(JoinPath(run.spec.dir, "efficiency_map.csv"));
    }
  }
  catch (const std::exception& exc) {
    std::cerr << exc.what() << std::endl;
    return;
  }

  std::vector<SweepSummary> summaries;
  for (const auto& run : runs) {
    summaries.push_back(BuildSweepSummary(run, "full", false));
    summaries.push_back(BuildSweepSummary(run, "fiducial", true));
  }

  const TString summaryCsv = JoinPath(outDir, "root_week11_divergence_sweep_summary.csv");
  WriteSummaryCsv(summaries, summaryCsv);
  const auto fiducial = ScopeRows(summaries, "fiducial");
  const auto full = ScopeRows(summaries, "full");
  DrawTrendPanel(fiducial,
                 outDir,
                 "root_week11_divergence_sweep_fiducial_trends",
                 false,
                 "Fiducial region");
  DrawTrendPanel(fiducial,
                 outDir,
                 "root_week11_divergence_sweep_fiducial_relative_to_100mrad",
                 true,
                 "Fiducial region");
  DrawTrendPanel(full,
                 outDir,
                 "root_week11_divergence_sweep_full_trends",
                 false,
                 "Full scan map");
  DrawTrendPanel(full,
                 outDir,
                 "root_week11_divergence_sweep_full_relative_to_100mrad",
                 true,
                 "Full scan map");
  PrintFiducialCore(fiducial);

  std::cout << "Wrote Week 11 divergence sweep plots to " << outDir << std::endl;
  std::cout << "Wrote summary CSV to " << summaryCsv << std::endl;
}

#endif
