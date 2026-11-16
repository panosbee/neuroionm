import fs from "node:fs";
import path from "node:path";

const rootDir = process.cwd();
const coordsPath = path.join(rootDir, "ionm-coords.json");
const seedPath = path.join(rootDir, "backend", "app", "seed_data", "ionm_seed.json");
const bodyImagePath = path.join(rootDir, "body.jpg");
const bodyAssetPath = path.join(rootDir, "bodymap-asset.json");
const recommendedImageWidth = 1600;
const recommendedImageHeight = 1600;
const recommendedLongEdge = 2400;

const coordinates = JSON.parse(fs.readFileSync(coordsPath, "utf8"));
const seed = JSON.parse(fs.readFileSync(seedPath, "utf8"));
const bodyImageSize = readImageSize(bodyImagePath);
const bodyAsset = fs.existsSync(bodyAssetPath) ? JSON.parse(fs.readFileSync(bodyAssetPath, "utf8")) : null;

const muscles = new Set((seed.muscles || []).map((item) => item.lat_name));
const coordinateMuscles = new Set(Object.keys(coordinates));
const linkedMuscles = new Set(
  (seed.knowledge_nodes || []).flatMap((node) => node.linked_muscles || []),
);
const allowedSides = new Set(["L", "R", "C"]);
const allowedViews = new Set(["anterior", "posterior"]);
const errors = [];
const warnings = [];
let pinCount = 0;

for (const [latName, pins] of Object.entries(coordinates)) {
  if (!muscles.has(latName)) errors.push(`Coordinate muscle is not present in seed.muscles: ${latName}`);
  if (!Array.isArray(pins) || pins.length === 0) {
    errors.push(`Coordinate muscle has no pins: ${latName}`);
    continue;
  }
  for (const [index, pin] of pins.entries()) {
    pinCount += 1;
    const label = `${latName}[${index}]`;
    if (!allowedViews.has(pin.view)) errors.push(`${label} has invalid view: ${pin.view}`);
    if (!allowedSides.has(pin.side)) errors.push(`${label} has invalid side: ${pin.side}`);
    if (!Number.isFinite(pin.x) || pin.x < 0 || pin.x > 100) errors.push(`${label} has invalid x: ${pin.x}`);
    if (!Number.isFinite(pin.y) || pin.y < 0 || pin.y > 100) errors.push(`${label} has invalid y: ${pin.y}`);
  }
}

for (const latName of linkedMuscles) {
  if (!muscles.has(latName)) errors.push(`Linked muscle is not present in seed.muscles: ${latName}`);
  if (!coordinateMuscles.has(latName)) warnings.push(`Linked muscle has no body-map coordinate yet: ${latName}`);
}

const duplicateCoordinates = findDuplicateCoordinates(coordinates);
for (const duplicate of duplicateCoordinates) {
  warnings.push(`Shared coordinate ${duplicate.coordinate}: ${duplicate.muscles.join(" | ")}`);
}

if ((bodyImageSize.width < recommendedImageWidth || bodyImageSize.height < recommendedImageHeight) && Math.max(bodyImageSize.width, bodyImageSize.height) < recommendedLongEdge) {
  warnings.push(
    `body.jpg is ${bodyImageSize.width}x${bodyImageSize.height}; focus views are production-grade only with a curated high-resolution anatomical asset of at least ${recommendedImageWidth}x${recommendedImageHeight} or long edge >= ${recommendedLongEdge}`,
  );
}

if (!bodyAsset) {
  warnings.push("bodymap-asset.json is missing; production body-map asset source/license/view bounds are not documented");
} else {
  if (bodyAsset.width !== bodyImageSize.width || bodyAsset.height !== bodyImageSize.height) {
    errors.push(`bodymap-asset.json dimensions ${bodyAsset.width}x${bodyAsset.height} do not match body.jpg ${bodyImageSize.width}x${bodyImageSize.height}`);
  }
  if (bodyAsset.status !== "production_coordinates_curated") {
    warnings.push(`body-map asset status is ${bodyAsset.status}; coordinates require re-curation before production training use`);
  }
  for (const viewName of ["anterior", "posterior", "cranial", "upper_limb", "lumbosacral", "lower_limb"]) {
    const bounds = bodyAsset.view_bounds?.[viewName];
    if (!bounds) {
      errors.push(`bodymap-asset.json missing view_bounds.${viewName}`);
    } else if (![bounds.x1, bounds.y1, bounds.x2, bounds.y2].every((value) => Number.isFinite(value) && value >= 0 && value <= 100) || bounds.x1 >= bounds.x2 || bounds.y1 >= bounds.y2) {
      errors.push(`bodymap-asset.json invalid view_bounds.${viewName}`);
    }
  }
}

console.log(`body_image: ${bodyImageSize.width}x${bodyImageSize.height}`);
if (bodyAsset) console.log(`body_asset_status: ${bodyAsset.status}`);
console.log(`coordinate_muscles: ${coordinateMuscles.size}`);
console.log(`pins: ${pinCount}`);
console.log(`linked_muscles: ${linkedMuscles.size}`);
console.log(`warnings: ${warnings.length}`);
console.log(`errors: ${errors.length}`);

if (warnings.length) {
  console.log("\nWarnings:");
  warnings.forEach((warning) => console.log(`- ${warning}`));
}

if (errors.length) {
  console.error("\nErrors:");
  errors.forEach((error) => console.error(`- ${error}`));
  process.exit(1);
}

function findDuplicateCoordinates(payload) {
  const byCoordinate = new Map();
  for (const [latName, pins] of Object.entries(payload)) {
    for (const pin of pins) {
      const key = `${pin.view}:${pin.side}:${pin.x}:${pin.y}`;
      const item = byCoordinate.get(key) || [];
      item.push(latName);
      byCoordinate.set(key, item);
    }
  }
  return [...byCoordinate.entries()]
    .filter(([, musclesAtCoordinate]) => musclesAtCoordinate.length > 1)
    .map(([coordinate, musclesAtCoordinate]) => ({ coordinate, muscles: musclesAtCoordinate }));
}

function readImageSize(filePath) {
  const buffer = fs.readFileSync(filePath);
  if (buffer.length >= 24 && buffer.toString("ascii", 1, 4) === "PNG") {
    return { width: buffer.readUInt32BE(16), height: buffer.readUInt32BE(20) };
  }
  if (buffer[0] === 0xff && buffer[1] === 0xd8) {
    let offset = 2;
    while (offset < buffer.length) {
      while (buffer[offset] === 0xff) offset += 1;
      const marker = buffer[offset];
      offset += 1;
      if (marker === 0xd9 || marker === 0xda) break;
      const segmentLength = buffer.readUInt16BE(offset);
      if (isStartOfFrameMarker(marker)) {
        return {
          width: buffer.readUInt16BE(offset + 5),
          height: buffer.readUInt16BE(offset + 3),
        };
      }
      offset += segmentLength;
    }
  }
  throw new Error(`Could not read image dimensions: ${filePath}`);
}

function isStartOfFrameMarker(marker) {
  return marker >= 0xc0 && marker <= 0xcf && ![0xc4, 0xc8, 0xcc].includes(marker);
}