// Móc nạp module: "zca-js" -> Zalo giả (chỉ dùng khi test)
export async function resolve(specifier, context, next) {
  if (specifier === "zca-js") return { url: new URL("./zca-js.mjs", import.meta.url).href, shortCircuit: true };
  return next(specifier, context);
}
