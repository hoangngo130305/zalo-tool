import path from "node:path";
import fs from "node:fs";
import { fileURLToPath } from "node:url";
import XLSX from "xlsx";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const out = path.join(ROOT, "data", "danh-sach.xlsx");
fs.mkdirSync(path.dirname(out), { recursive: true });

if (fs.existsSync(out)) {
  console.log("data/danh-sach.xlsx đã tồn tại, không ghi đè.");
  process.exit(0);
}

// Cột SĐT để dạng text để Excel không làm mất số 0 đầu
const ws = XLSX.utils.aoa_to_sheet([
  ["SĐT", "Tên"],
  ["0901234567", "Anh Nam"],
  ["0912345678", "Chị Lan"],
]);
ws["!cols"] = [{ wch: 14 }, { wch: 24 }];
for (const cell of ["A2", "A3"]) ws[cell].z = "@";

const wb = XLSX.utils.book_new();
XLSX.utils.book_append_sheet(wb, ws, "DanhSach");
XLSX.writeFile(wb, out);
console.log("Đã tạo file mẫu:", out);
