import fs from "node:fs";
import path from "node:path";

const rootDir = process.cwd();
const sourceDir = path.join(rootDir, "node_modules", "stockfish", "src");
const targetDir = path.join(rootDir, "public", "stockfish");

const filesToCopy = [
  "stockfish-nnue-16-single.js",
  "stockfish-nnue-16-single.wasm",
];

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

function copyIfExists(sourcePath, targetPath) {
  if (!fs.existsSync(sourcePath)) {
    console.warn(`[stockfish] Missing source asset: ${sourcePath}`);
    return;
  }
  fs.copyFileSync(sourcePath, targetPath);
}

function main() {
  ensureDir(targetDir);
  for (const fileName of filesToCopy) {
    const sourcePath = path.join(sourceDir, fileName);
    const targetPath = path.join(targetDir, fileName);
    copyIfExists(sourcePath, targetPath);
  }
}

main();
