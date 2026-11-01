// node --import ./tests/zalo-gia/dang-ky.mjs src/worker.js
// Thay zca-js bằng Zalo giả và cho đồng hồ chạy nhanh gấp TANG_TOC lần (thời gian chờ 60 giây còn 0,3 giây),
// để test cả luồng gửi thật mà không cần tài khoản Zalo.
import { register } from "node:module";

register("./moc.mjs", import.meta.url);

const TANG_TOC = Number(process.env.TANG_TOC || 200);
const goc = Date.now();
const nowThat = Date.now.bind(Date);
Date.now = () => goc + (nowThat() - goc) * TANG_TOC;
const setTimeoutThat = globalThis.setTimeout;
globalThis.setTimeout = (fn, ms = 0, ...a) => setTimeoutThat(fn, Math.max(0, ms / TANG_TOC), ...a);
