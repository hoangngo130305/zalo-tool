// Zalo giả cho test đầu-cuối (thay thư viện zca-js khi chạy worker với --import ./tests/zalo-gia/dang-ky.mjs).
// Theo SĐT: đuôi 99 -> Zalo báo lỗi (như khi bị giới hạn tìm số), đuôi 88 -> chưa đăng ký Zalo, đuôi 77 -> khách chặn
// tin từ người lạ (gửi tin lỗi cho tới khi đồng ý kết bạn), còn lại -> gửi được.
// Mọi lời mời / tin nhắn "đã gửi" được ghi vào data/zalo-gia-da-gui.jsonl để test kiểm tra.
// Test giả lập khách đồng ý kết bạn / nhắn tin bằng cách ghi 1 dòng vào data/zalo-gia-su-kien.jsonl:
//   {"loai": "dong_y", "uid": "u84901000001"}  hoặc  {"loai": "nhan_tin", "uid": "u84901000001"}
import fs from "node:fs";
import path from "node:path";
import { EventEmitter } from "node:events";

export const ThreadType = { User: 0, Group: 1 };
export const LoginQRCallbackEventType = {
  QRCodeGenerated: 0, QRCodeExpired: 1, QRCodeScanned: 2, QRCodeDeclined: 3, GotLoginInfo: 4,
};
export const FriendEventType = { ADD: 0, REMOVE: 1, REQUEST: 2 };
const DATA = path.join(process.cwd(), "data");
const LOG = path.join(DATA, "zalo-gia-da-gui.jsonl");
const SU_KIEN = path.join(DATA, "zalo-gia-su-kien.jsonl");
const ghi = (x) => fs.appendFileSync(LOG, JSON.stringify(x) + "\n");
const banBe = new Set();      // uid đã đồng ý kết bạn

function listener() {
  const l = new EventEmitter();
  let docDen = 0;
  l.start = () => {
    l.timer = setInterval(() => {
      if (!fs.existsSync(SU_KIEN)) return;
      const dong = fs.readFileSync(SU_KIEN, "utf8").split("\n").filter(Boolean);
      for (const s of dong.slice(docDen)) {
        const ev = JSON.parse(s);
        if (ev.loai === "dong_y") {
          banBe.add(ev.uid);
          l.emit("friend_event", { type: FriendEventType.ADD, data: ev.uid, threadId: ev.uid, isSelf: false });
        } else if (ev.loai === "nhan_tin") {
          l.emit("message", { type: ThreadType.User, threadId: ev.uid, isSelf: false, data: { content: "(bí mật)" } });
        }
      }
      docDen = dong.length;
    }, 200);
  };
  l.stop = () => clearInterval(l.timer);
  return l;
}

function api() {
  return {
    listener: listener(),
    fetchAccountInfo: async () => ({ profile: { displayName: "Zalo giả" } }),
    findUser: async (phone) => {
      if (phone.endsWith("99")) throw new Error("Zalo giả: vượt giới hạn tìm kiếm");
      if (phone.endsWith("88")) return {};
      return { uid: `u${phone}`, display_name: `KH ${phone}` };
    },
    sendFriendRequest: async (msg, uid) => ghi({ loai: "ket-ban", uid, msg }),
    changeFriendAlias: async (alias, uid) => ghi({ loai: "ten-goi-nho", uid, alias }),
    sendMessage: async (m, uid) => {
      if (String(uid).endsWith("77") && !banBe.has(uid)) throw new Error("Zalo giả: khách chặn tin từ người lạ");
      ghi({ loai: "tin-nhan", uid, msg: typeof m === "string" ? m : m.msg, anh: m?.attachments?.length || 0 });
    },
    getAllGroups: async () => ({ gridVerMap: { 111: "1", 222: "1" } }),
    getGroupInfo: async () => ({ gridInfoMap: { 111: { name: "Nhóm BĐS Q7", totalMember: 50 }, 222: { name: "Nhóm đầu tư", totalMember: 20 } } }),
  };
}

export class Zalo {
  // Phiên ghi {"hetHan": true} thì coi như phiên đã hết hạn
  async login(creds) {
    if (creds?.hetHan) throw new Error("Zalo giả: phiên đã hết hạn");
    return api();
  }
  async loginQR() { throw new Error("Zalo giả không hỗ trợ QR"); }
}
