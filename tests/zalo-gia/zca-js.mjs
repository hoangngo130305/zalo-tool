// Zalo giả cho test đầu-cuối (thay thư viện zca-js khi chạy worker với --import ./tests/zalo-gia/dang-ky.mjs).
// Theo SĐT: đuôi 99 -> Zalo báo lỗi (như khi bị giới hạn tìm số), đuôi 88 -> chưa đăng ký Zalo, còn lại -> gửi được.
// Mọi lời mời / tin nhắn "đã gửi" được ghi vào data/zalo-gia-da-gui.jsonl để test kiểm tra.
import fs from "node:fs";
import path from "node:path";

export const ThreadType = { User: 0, Group: 1 };
export const LoginQRCallbackEventType = {
  QRCodeGenerated: 0, QRCodeExpired: 1, QRCodeScanned: 2, QRCodeDeclined: 3, GotLoginInfo: 4,
};
const LOG = path.join(process.cwd(), "data", "zalo-gia-da-gui.jsonl");
const ghi = (x) => fs.appendFileSync(LOG, JSON.stringify(x) + "\n");

function api() {
  return {
    fetchAccountInfo: async () => ({ profile: { displayName: "Zalo giả" } }),
    findUser: async (phone) => {
      if (phone.endsWith("99")) throw new Error("Zalo giả: vượt giới hạn tìm kiếm");
      if (phone.endsWith("88")) return {};
      return { uid: `u${phone}`, display_name: `KH ${phone}` };
    },
    sendFriendRequest: async (msg, uid) => ghi({ loai: "ket-ban", uid, msg }),
    changeFriendAlias: async (alias, uid) => ghi({ loai: "ten-goi-nho", uid, alias }),
    sendMessage: async (m, uid) => ghi({ loai: "tin-nhan", uid, msg: typeof m === "string" ? m : m.msg }),
    getAllGroups: async () => ({ gridVerMap: { 111: "1", 222: "1" } }),
    getGroupInfo: async () => ({ gridInfoMap: { 111: { name: "Nhóm BĐS Q7", totalMember: 50 }, 222: { name: "Nhóm đầu tư", totalMember: 20 } } }),
  };
}

export class Zalo {
  async login() { return api(); }
  async loginQR() { throw new Error("Zalo giả không hỗ trợ QR"); }
}
