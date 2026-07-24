#ifndef OPNOVICE2_PLOT_WEEK11_BEAM_SIGMA_SWEEP_C
#define OPNOVICE2_PLOT_WEEK11_BEAM_SIGMA_SWEEP_C

#include "../plot_efficiency_map.C"

#include <TGraph.h>
#include <TLegend.h>
#include <TROOT.h>
#include <TSystem.h>

#include <fstream>
#include <map>

namespace {

struct BeamSigmaSpec {
  TString label;
  TString dir;
  double sigmaMm = 0.0;
  double fiducialLimitMm = 45.0;
};

struct BeamSigmaRun {
  BeamSigmaSpec spec;
  std::vector<EfficiencyPoint> points;
};

struct BeamSigmaSummary {
  TString label;
  double sigmaMm = 0.0;
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

BeamSigmaSummary BuildBeamSigmaSummary(const BeamSigmaRun& run, const TString& scope, bool fiducial)
{
  const auto stats = SummarizeRegion(run.points, scope.Data(), run.spec.fiducialLimitMm, fiducial);
  BeamSigmaSummary row;
  row.label = run.spec.label;
  row.sigmaMm = run.spec.sigmaMm;
  row.scope = scope;
  row.n = stats.nPoints;
  row.meanEff = stats.collectionEfficiency.Mean();
  const double rms = stats.collectionEfficiency.Rms();
  row.rmsOverMean = std::isfinite(row.meanEff) && row.meanEff != 0.0 ? rms / row.meanEff : kNaN;
  row.detPerEvent = stats.detectedPerEvent.Mean();
  row.scintPerEvent = stats.scintillationPerEvent.Mean();
  return row;
}

double MetricValue(const BeamSigmaSummary& row, int metric)
{
  if (metric == 0) return row.meanEff * 100.0;
  if (metric == 1) return row.rmsOverMean * 100.0;
  if (metric == 2) return row.detPerEvent;
  return kNaN;
}

void WriteSummaryCsv(const std::vector<BeamSigmaSummary>& rows, const TString& outPath)
{
  std::ofstream out(outPath.Data());
  out << "label,beam_sigma_mm,scope,n_points,mean_eff,rms_over_mean,detected_per_event,"
      << "scintillation_per_event\n";
  out << std::setprecision(12);
  for (const auto& row : rows) {
    out << row.label << ','
        << row.sigmaMm << ','
        << row.scope << ','
        << row.n << ','
        << row.meanEff << ','
        << row.rmsOverMean << ','
        << row.detPerEvent << ','
        << row.scintPerEvent << '\n';
  }
}

std::vector<BeamSigmaSummary> ScopeRows(const std::vector<BeamSigmaSummary>& rows,
                                        const TString& scope)
{
  std::vector<BeamSigmaSummary> filtered;
  for (const auto& row : rows) {
    if (row.scope == scope) filtered.push_back(row);
  }
  return filtered;
}

std::vector<TString> LabelsInOrder(const std::vector<BeamSigmaSummary>& rows)
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

std::vector<BeamSigmaSummary> RowsForLabel(const std::vector<BeamSigmaSummary>& rows,
                                           const TString& label)
{
  std::vector<BeamSigmaSummary> selected;
  for (const auto& row : rows) {
    if (row.label == label) selected.push_back(row);
  }
  std::sort(selected.begin(), selected.end(), [](const BeamSigmaSummary& a,
                                                 const BeamSigmaSummary& b) {
    return a.sigmaMm < b.sigmaMm;
  });
  return selected;
}

double BaselineSigma0(const std::vector<BeamSigmaSummary>& rows, int metric)
{
  for (const auto& row : rows) {
    if (std::abs(row.sigmaMm) < 1.e-9) return MetricValue(row, metric);
  }
  return kNaN;
}

void RangeForMetric(const std::vector<BeamSigmaSummary>& rows,
                    int metric,
                    bool relativeToSigma0,
                    double& minValue,
                    double& maxValue)
{
  minValue = std::numeric_limits<double>::infinity();
  maxValue = -std::numeric_limits<double>::infinity();
  const auto labels = LabelsInOrder(rows);
  for (const auto& label : labels) {
    const auto selected = RowsForLabel(rows, label);
    const double baseline = relativeToSigma0 ? BaselineSigma0(selected, metric) : 1.0;
    for (const auto& row : selected) {
      double value = MetricValue(row, metric);
      if (relativeToSigma0) {
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

void DrawTrendPanel(const std::vector<BeamSigmaSummary>& rows,
                    const TString& outDir,
                    const TString& stem,
                    bool relativeToSigma0,
                    const TString& scopeLabel)
{
  const std::vector<MetricDef> metrics = {
    {"Mean collection efficiency", relativeToSigma0 ? "ratio to #sigma=0" : "collection efficiency [%]", 0},
    {"Spatial non-uniformity", relativeToSigma0 ? "ratio to #sigma=0" : "RMS / mean [%]", 1},
    {"Detected light yield", relativeToSigma0 ? "ratio to #sigma=0" : "detected photons / event", 2},
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
    RangeForMetric(rows, metric.index, relativeToSigma0, minValue, maxValue);
    auto* frame = gPad->DrawFrame(0.0,
                                  minValue,
                                  10.5,
                                  maxValue,
                                  Form("%s: %s;beam spot #sigma [mm];%s",
                                       scopeLabel.Data(),
                                       metric.title.Data(),
                                       metric.yTitle.Data()));
    frame->GetXaxis()->SetNdivisions(505);
    frame->GetYaxis()->SetTitleOffset(0.70);

    auto* legend = new TLegend(0.13, 0.60, 0.50, 0.88);
    legend->SetBorderSize(0);
    legend->SetFillStyle(0);
    legend->SetTextSize(0.033);

    for (size_t i = 0; i < labels.size(); ++i) {
      const auto selected = RowsForLabel(rows, labels[i]);
      const double baseline = relativeToSigma0 ? BaselineSigma0(selected, metric.index) : 1.0;
      auto* graph = new TGraph();
      int point = 0;
      for (const auto& row : selected) {
        double value = MetricValue(row, metric.index);
        if (relativeToSigma0) {
          if (!std::isfinite(baseline) || baseline == 0.0) continue;
          value /= baseline;
        }
        if (!std::isfinite(value)) continue;
        graph->SetPoint(point++, row.sigmaMm, value);
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

void PrintCore(const std::vector<BeamSigmaSummary>& rows, const TString& title)
{
  std::cout << title << std::endl;
  std::cout << std::setprecision(8);
  for (const auto& label : LabelsInOrder(rows)) {
    const auto selected = RowsForLabel(rows, label);
    std::cout << label << std::endl;
    for (const auto& row : selected) {
      std::cout << "  sigma=" << std::setw(2) << row.sigmaMm << " mm: "
                << "mean_eff = " << row.meanEff
                << ", rms/mean = " << row.rmsOverMean
                << ", det/event = " << row.detPerEvent
                << std::endl;
    }
  }
}

}  // namespace

void plot_week11_beam_sigma_sweep(const char* baseDirArg = "week11_run",
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

  std::vector<BeamSigmaRun> runs = {
    {{"50x50x16 back", JoinPath(baseDir, "week11_nominal_50x50x16_center_back"), 0.0, 20.0}, {}},
    {{"50x50x16 back", JoinPath(baseDir, "week11_beam_sigma_50x50x16_center_back_sigma2mm_100mrad"), 2.0, 20.0}, {}},
    {{"50x50x16 back", JoinPath(baseDir, "week11_beam_sigma_50x50x16_center_back_sigma5mm_100mrad"), 5.0, 20.0}, {}},
    {{"50x50x16 back", JoinPath(baseDir, "week11_beam_sigma_50x50x16_center_back_sigma10mm_100mrad"), 10.0, 20.0}, {}},

    {{"100x100x16 back", JoinPath(baseDir, "week11_nominal_100x100x16_center_back"), 0.0, 45.0}, {}},
    {{"100x100x16 back", JoinPath(baseDir, "week11_beam_sigma_100x100x16_center_back_sigma2mm_100mrad"), 2.0, 45.0}, {}},
    {{"100x100x16 back", JoinPath(baseDir, "week11_beam_sigma_100x100x16_center_back_sigma5mm_100mrad"), 5.0, 45.0}, {}},
    {{"100x100x16 back", JoinPath(baseDir, "week11_beam_sigma_100x100x16_center_back_sigma10mm_100mrad"), 10.0, 45.0}, {}},

    {{"100x100x16 side", JoinPath(baseDir, "week11_nominal_100x100x16_center_side_100mrad"), 0.0, 45.0}, {}},
    {{"100x100x16 side", JoinPath(baseDir, "week11_beam_sigma_100x100x16_center_side_sigma2mm_100mrad"), 2.0, 45.0}, {}},
    {{"100x100x16 side", JoinPath(baseDir, "week11_beam_sigma_100x100x16_center_side_sigma5mm_100mrad"), 5.0, 45.0}, {}},
    {{"100x100x16 side", JoinPath(baseDir, "week11_beam_sigma_100x100x16_center_side_sigma10mm_100mrad"), 10.0, 45.0}, {}},

    {{"100x100x24 back", JoinPath(baseDir, "week11_nominal_100x100x24_center_back"), 0.0, 45.0}, {}},
    {{"100x100x24 back", JoinPath(baseDir, "week11_beam_sigma_100x100x24_center_back_sigma2mm_100mrad"), 2.0, 45.0}, {}},
    {{"100x100x24 back", JoinPath(baseDir, "week11_beam_sigma_100x100x24_center_back_sigma5mm_100mrad"), 5.0, 45.0}, {}},
    {{"100x100x24 back", JoinPath(baseDir, "week11_beam_sigma_100x100x24_center_back_sigma10mm_100mrad"), 10.0, 45.0}, {}},

    {{"100x100x24 side", JoinPath(baseDir, "week11_nominal_100x100x24_center_side"), 0.0, 45.0}, {}},
    {{"100x100x24 side", JoinPath(baseDir, "week11_beam_sigma_100x100x24_center_side_sigma2mm_100mrad"), 2.0, 45.0}, {}},
    {{"100x100x24 side", JoinPath(baseDir, "week11_beam_sigma_100x100x24_center_side_sigma5mm_100mrad"), 5.0, 45.0}, {}},
    {{"100x100x24 side", JoinPath(baseDir, "week11_beam_sigma_100x100x24_center_side_sigma10mm_100mrad"), 10.0, 45.0}, {}},

    {{"100x100x24 dimple", JoinPath(baseDir, "week11_nominal_100x100x24_center_back_dimple_r3mm"), 0.0, 45.0}, {}},
    {{"100x100x24 dimple", JoinPath(baseDir, "week11_beam_sigma_100x100x24_center_back_dimple_r3mm_sigma2mm_100mrad"), 2.0, 45.0}, {}},
    {{"100x100x24 dimple", JoinPath(baseDir, "week11_beam_sigma_100x100x24_center_back_dimple_r3mm_sigma5mm_100mrad"), 5.0, 45.0}, {}},
    {{"100x100x24 dimple", JoinPath(baseDir, "week11_beam_sigma_100x100x24_center_back_dimple_r3mm_sigma10mm_100mrad"), 10.0, 45.0}, {}},
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

  std::vector<BeamSigmaSummary> summaries;
  for (const auto& run : runs) {
    summaries.push_back(BuildBeamSigmaSummary(run, "full", false));
    summaries.push_back(BuildBeamSigmaSummary(run, "fiducial", true));
  }

  const TString summaryCsv = JoinPath(outDir, "root_week11_beam_sigma_sweep_summary.csv");
  WriteSummaryCsv(summaries, summaryCsv);
  const auto fiducial = ScopeRows(summaries, "fiducial");
  const auto full = ScopeRows(summaries, "full");
  DrawTrendPanel(fiducial,
                 outDir,
                 "root_week11_beam_sigma_sweep_fiducial_trends",
                 false,
                 "Fiducial region");
  DrawTrendPanel(fiducial,
                 outDir,
                 "root_week11_beam_sigma_sweep_fiducial_relative_to_sigma0",
                 true,
                 "Fiducial region");
  DrawTrendPanel(full,
                 outDir,
                 "root_week11_beam_sigma_sweep_full_trends",
                 false,
                 "Full scan map");
  DrawTrendPanel(full,
                 outDir,
                 "root_week11_beam_sigma_sweep_full_relative_to_sigma0",
                 true,
                 "Full scan map");
  PrintCore(fiducial, "Fiducial core summary:");

  std::cout << "Wrote Week 11 beam sigma sweep plots to " << outDir << std::endl;
  std::cout << "Wrote summary CSV to " << summaryCsv << std::endl;
}

#endif
