#ifndef OPNOVICE2_PLOT_SIPM_UNIFORMITY_C
#define OPNOVICE2_PLOT_SIPM_UNIFORMITY_C

#include <TCanvas.h>
#include <TF1.h>
#include <TFile.h>
#include <TGraph.h>
#include <TGraphErrors.h>
#include <TH1.h>
#include <TH2D.h>
#include <TString.h>
#include <TStyle.h>
#include <TSystem.h>

#include <algorithm>
#include <cctype>
#include <cmath>
#include <fstream>
#include <iostream>
#include <limits>
#include <set>
#include <sstream>
#include <string>
#include <utility>
#include <vector>

namespace {

struct ScanPoint {
  std::string tag;
  double x = 0.0;
  double y = 0.0;
  double z = 0.0;
  std::string unit;
  std::string macro;
  std::string root;
  std::string log;
};

std::vector<std::string> SplitCsvLine(const std::string& line)
{
  std::vector<std::string> fields;
  std::stringstream ss(line);
  std::string field;
  while (std::getline(ss, field, ',')) {
    fields.push_back(field);
  }
  return fields;
}

std::string ReadTextFile(const TString& path)
{
  std::ifstream in(path.Data());
  if (!in) return "";
  std::ostringstream ss;
  ss << in.rdbuf();
  return ss.str();
}

std::string JsonStringValue(const std::string& json, const std::string& key)
{
  const std::string pattern = "\"" + key + "\"";
  const auto keyPos = json.find(pattern);
  if (keyPos == std::string::npos) return "";

  const auto colonPos = json.find(':', keyPos + pattern.size());
  if (colonPos == std::string::npos) return "";

  const auto quotePos = json.find('"', colonPos + 1);
  if (quotePos == std::string::npos) return "";

  std::string value;
  bool escaped = false;
  for (size_t i = quotePos + 1; i < json.size(); ++i) {
    const char c = json[i];
    if (escaped) {
      value.push_back(c);
      escaped = false;
    }
    else if (c == '\\') {
      escaped = true;
    }
    else if (c == '"') {
      return value;
    }
    else {
      value.push_back(c);
    }
  }
  return "";
}

double JsonNumberValue(const std::string& json, const std::string& key, double fallback)
{
  const std::string pattern = "\"" + key + "\"";
  const auto keyPos = json.find(pattern);
  if (keyPos == std::string::npos) return fallback;

  const auto colonPos = json.find(':', keyPos + pattern.size());
  if (colonPos == std::string::npos) return fallback;

  size_t start = json.find_first_of("-0123456789.", colonPos + 1);
  if (start == std::string::npos) return fallback;

  size_t end = start;
  while (end < json.size()) {
    const char c = json[end];
    if (!(std::isdigit(c) || c == '-' || c == '+' || c == '.' || c == 'e' || c == 'E')) break;
    ++end;
  }

  try {
    return std::stod(json.substr(start, end - start));
  }
  catch (...) {
    return fallback;
  }
}

bool JsonBoolValue(const std::string& json, const std::string& key, bool fallback)
{
  const std::string pattern = "\"" + key + "\"";
  const auto keyPos = json.find(pattern);
  if (keyPos == std::string::npos) return fallback;

  const auto colonPos = json.find(':', keyPos + pattern.size());
  if (colonPos == std::string::npos) return fallback;

  const auto valuePos = json.find_first_not_of(" \t\r\n", colonPos + 1);
  if (valuePos == std::string::npos) return fallback;

  if (json.compare(valuePos, 4, "true") == 0) return true;
  if (json.compare(valuePos, 5, "false") == 0) return false;
  return fallback;
}

std::vector<ScanPoint> ReadPointsCsv(const TString& path)
{
  std::vector<ScanPoint> points;
  std::ifstream in(path.Data());
  if (!in) {
    std::cerr << "Cannot open points CSV: " << path << std::endl;
    return points;
  }

  std::string line;
  std::getline(in, line);  // header
  while (std::getline(in, line)) {
    if (line.empty()) continue;
    const auto fields = SplitCsvLine(line);
    if (fields.size() != 8) {
      std::cerr << "Skipping malformed points row: " << line << std::endl;
      continue;
    }

    ScanPoint p;
    p.tag = fields[0];
    p.x = std::stod(fields[1]);
    p.y = std::stod(fields[2]);
    p.z = std::stod(fields[3]);
    p.unit = fields[4];
    p.macro = fields[5];
    p.root = fields[6];
    p.log = fields[7];
    points.push_back(p);
  }
  return points;
}

TH1* GetDetectedHist(TFile& f)
{
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

double UnitToCm(double value, const std::string& unit)
{
  if (unit == "mm") return value * 0.1;
  if (unit == "cm") return value;
  if (unit == "m") return value * 100.0;
  return value;
}

double FromCm(double valueCm, const std::string& unit)
{
  if (unit == "mm") return valueCm * 10.0;
  if (unit == "cm") return valueCm;
  if (unit == "m") return valueCm * 0.01;
  return valueCm;
}

bool ParseVectorWithUnit(const std::string& text,
                         double& x,
                         double& y,
                         double& z,
                         std::string& unit)
{
  std::stringstream ss(text);
  ss >> x >> y >> z >> unit;
  return !ss.fail() && !unit.empty();
}

double ConvertUnit(double value, const std::string& fromUnit, const std::string& toUnit)
{
  return FromCm(UnitToCm(value, fromUnit), toUnit);
}

std::pair<double, double> EstimateSiPMProjection(const std::string& face,
                                                 const std::string& localPosition,
                                                 const std::string& size,
                                                 const std::string& targetUnit)
{
  double lx = 0.0;
  double ly = 0.0;
  double lz = 0.0;
  std::string localUnit = targetUnit;
  ParseVectorWithUnit(localPosition, lx, ly, lz, localUnit);

  double activeU = 0.0;
  double activeV = 0.0;
  double thickness = 0.0;
  std::string sizeUnit = targetUnit;
  ParseVectorWithUnit(size, activeU, activeV, thickness, sizeUnit);

  const double localX = ConvertUnit(lx, localUnit, targetUnit);
  const double localY = ConvertUnit(ly, localUnit, targetUnit);
  const double halfThickness = 0.5 * ConvertUnit(thickness, sizeUnit, targetUnit);
  const double tankHalfX = FromCm(5.0, targetUnit);
  const double tankHalfY = FromCm(5.0, targetUnit);

  if (face == "+X") return {tankHalfX + halfThickness, localX};
  if (face == "-X") return {-tankHalfX - halfThickness, localX};
  if (face == "+Y") return {localX, tankHalfY + halfThickness};
  if (face == "-Y") return {localX, -tankHalfY - halfThickness};
  return {localX, localY};
}

std::vector<double> UniqueSortedValues(const std::vector<ScanPoint>& points, bool useX)
{
  std::set<double> values;
  for (const auto& p : points) {
    values.insert(useX ? p.x : p.y);
  }
  return std::vector<double>(values.begin(), values.end());
}

std::vector<double> MakeBinEdges(const std::vector<double>& centers)
{
  std::vector<double> edges;
  if (centers.empty()) return edges;
  if (centers.size() == 1) {
    edges.push_back(centers.front() - 0.5);
    edges.push_back(centers.front() + 0.5);
    return edges;
  }

  edges.push_back(centers.front() - 0.5 * (centers[1] - centers[0]));
  for (size_t i = 1; i < centers.size(); ++i) {
    edges.push_back(0.5 * (centers[i - 1] + centers[i]));
  }
  edges.push_back(centers.back() + 0.5 * (centers.back() - centers[centers.size() - 2]));
  return edges;
}

double MinPositiveSpacing(const std::vector<double>& centers)
{
  double spacing = std::numeric_limits<double>::max();
  for (size_t i = 1; i < centers.size(); ++i) {
    const double delta = centers[i] - centers[i - 1];
    if (delta > 0.0) spacing = std::min(spacing, delta);
  }
  if (spacing == std::numeric_limits<double>::max()) return 1.0;
  return spacing;
}

void SaveCanvas(TCanvas* canvas, const TString& runDir, const TString& stem)
{
  canvas->SaveAs(Form("%s/%s.pdf", runDir.Data(), stem.Data()));
  canvas->SaveAs(Form("%s/%s.png", runDir.Data(), stem.Data()));
}

TString CleanTitle(const std::string& text, const char* fallback)
{
  if (text.empty()) return fallback;
  return TString(text.c_str());
}

}  // namespace

void plot_sipm_uniformity(const char* runDirArg = "scan_latest")
{
  gStyle->SetOptStat(0);

  TString runDir(runDirArg);
  if (runDir.EndsWith("/")) runDir.Chop();

  const TString pointsPath = Form("%s/points.csv", runDir.Data());
  const TString configPath = Form("%s/run_config.json", runDir.Data());

  const auto points = ReadPointsCsv(pointsPath);
  if (points.empty()) {
    std::cerr << "No scan points found in " << pointsPath << std::endl;
    return;
  }

  const std::string config = ReadTextFile(configPath);
  const std::string scanName = JsonStringValue(config, "scan_name");
  const std::string mode = JsonStringValue(config, "mode");
  const std::string gridName = JsonStringValue(config, "grid_name");
  const std::string sipmFace = JsonStringValue(config, "face");
  const std::string sipmLocal = JsonStringValue(config, "local_position");
  const std::string sipmSize = JsonStringValue(config, "size");
  const double nEvents = JsonNumberValue(config, "events_per_point", 100.0);
  const bool dryRun = JsonBoolValue(config, "dry_run", false);

  const std::string unit = points.front().unit;
  const auto xs = UniqueSortedValues(points, true);
  const auto ys = UniqueSortedValues(points, false);
  const auto xEdges = MakeBinEdges(xs);
  const auto yEdges = MakeBinEdges(ys);

  const auto sipmXY = EstimateSiPMProjection(sipmFace, sipmLocal, sipmSize, unit);

  std::vector<std::pair<double, double>> distPoints;

  TH2D* hMap = new TH2D(
    "hMap",
    Form("%s;electron x position [%s];electron y position [%s];#LT N_{SiPM} #GT / event",
         CleanTitle(scanName, "SiPM collection uniformity").Data(),
         unit.c_str(),
         unit.c_str()),
    static_cast<int>(xs.size()), xEdges.data(),
    static_cast<int>(ys.size()), yEdges.data()
  );

  int nLoaded = 0;
  if (dryRun) {
    std::cout << "Run is marked dry_run; skipping ROOT file loading." << std::endl;
  }
  else {
    for (const auto& p : points) {
      if (gSystem->AccessPathName(p.root.c_str())) {
        std::cerr << "Cannot open " << p.root << std::endl;
        continue;
      }

      TFile f(p.root.c_str(), "READ");
      if (f.IsZombie()) {
        std::cerr << "Cannot open " << p.root << std::endl;
        continue;
      }

      TH1* hDet = GetDetectedHist(f);
      if (!hDet) {
        std::cerr << "Cannot find detected photon histogram in " << p.root << std::endl;
        continue;
      }

      const double avgDetected = hDet->GetEntries() / nEvents;
      const double dist = std::sqrt((p.x - sipmXY.first) * (p.x - sipmXY.first) +
                                    (p.y - sipmXY.second) * (p.y - sipmXY.second));
      distPoints.emplace_back(dist, avgDetected);

      const int bx = hMap->GetXaxis()->FindBin(p.x);
      const int by = hMap->GetYaxis()->FindBin(p.y);
      hMap->SetBinContent(bx, by, avgDetected);
      ++nLoaded;
    }
  }

  if (!dryRun && nLoaded == 0) {
    std::cerr << "No ROOT files were loaded. The plots will be empty." << std::endl;
  }

  gStyle->SetPaintTextFormat(".2f");

  TCanvas* c = new TCanvas("c", "SiPM uniformity", 1100, 850);
  c->SetLeftMargin(0.12);
  c->SetRightMargin(0.18);
  c->SetBottomMargin(0.12);
  c->SetTopMargin(0.10);

  hMap->GetZaxis()->SetTitle("#LT N_{SiPM} #GT / event");
  hMap->GetZaxis()->SetTitleOffset(1.25);
  hMap->GetZaxis()->SetTitleSize(0.040);
  hMap->GetZaxis()->SetLabelSize(0.040);
  hMap->GetXaxis()->SetTitleSize(0.045);
  hMap->GetYaxis()->SetTitleSize(0.045);
  hMap->GetXaxis()->SetLabelSize(0.040);
  hMap->GetYaxis()->SetLabelSize(0.040);
  hMap->Draw("COLZ TEXT");

  SaveCanvas(c, runDir, "sipm_uniformity_map");

  if (distPoints.empty()) {
    std::cout << "Loaded 0 / " << points.size() << " scan points from "
              << pointsPath << std::endl;
    std::cout << "Saved empty map to " << runDir << std::endl;
    return;
  }

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

  gDist->SetTitle(Form("%s response vs distance;distance to SiPM projection [%s];#LT N_{SiPM} #GT / event",
                       CleanTitle(scanName, "SiPM").Data(),
                       unit.c_str()));
  gDist->SetMarkerStyle(20);
  gDist->SetMarkerSize(0.9);
  gDist->Draw("AP");

  SaveCanvas(cDist, runDir, "sipm_response_vs_distance");

  const double xSpacing = MinPositiveSpacing(xs);
  const double ySpacing = MinPositiveSpacing(ys);
  const double binWidth = std::min(xSpacing, ySpacing);
  double maxDistance = 0.0;
  for (const auto& p : distPoints) {
    maxDistance = std::max(maxDistance, p.first);
  }
  const double rMin = 0.0;
  const double rMax = std::max(binWidth, maxDistance + 0.5 * binWidth);
  const int nRBins = std::max(1, static_cast<int>(std::ceil((rMax - rMin) / binWidth)));

  std::vector<int> counts(nRBins, 0);
  std::vector<double> sumY(nRBins, 0.0);
  std::vector<double> sumY2(nRBins, 0.0);

  for (const auto& p : distPoints) {
    const int ibin = static_cast<int>((p.first - rMin) / binWidth);
    if (ibin < 0 || ibin >= nRBins) continue;
    counts[ibin] += 1;
    sumY[ibin] += p.second;
    sumY2[ibin] += p.second * p.second;
  }

  std::vector<double> rCenters;
  std::vector<double> yMeans;
  std::vector<double> rErrors;
  std::vector<double> yErrors;

  for (int i = 0; i < nRBins; ++i) {
    if (counts[i] == 0) continue;

    const double mean = sumY[i] / counts[i];
    const double mean2 = sumY2[i] / counts[i];
    double rms = 0.0;
    if (counts[i] > 1) {
      rms = std::sqrt(std::max(0.0, mean2 - mean * mean));
    }

    rCenters.push_back(rMin + (i + 0.5) * binWidth);
    yMeans.push_back(mean);
    rErrors.push_back(0.5 * binWidth);
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

  gAvg->SetTitle(Form("%s distance-binned response;distance to SiPM projection [%s];#LT N_{SiPM} #GT / event",
                      CleanTitle(scanName, "SiPM").Data(),
                      unit.c_str()));
  gAvg->SetMarkerStyle(20);
  gAvg->SetMarkerSize(1.0);
  gAvg->Draw("AP");

  if (rCenters.size() >= 3 && yMeans.size() >= 3) {
    TF1* fExp = new TF1("fExp", "[0]*exp(-x/[1]) + [2]", rMin, rMax);
    fExp->SetParNames("A", "lambda_eff", "C");
    fExp->SetParameters(yMeans.front(), std::max(binWidth, 2.0 * binWidth), yMeans.back());
    gAvg->Fit(fExp, "RQ");
    fExp->Draw("same");
  }

  SaveCanvas(cAvg, runDir, "sipm_response_vs_distance_binned");

  std::cout << "Loaded " << nLoaded << " / " << points.size() << " scan points from "
            << pointsPath << std::endl;
  std::cout << "Saved plots to " << runDir << std::endl;
  if (!mode.empty() || !gridName.empty()) {
    std::cout << "Scan mode/grid: " << mode << " / " << gridName << std::endl;
  }
}

#endif
