// 本地加密保险箱：AES-256-GCM + scrypt 派生，密钥与密文分开存放。
//
// 文件格式（二进制）：
//   magic "SMV1"(4) | headerLen(4, BE) | header JSON(utf8) | salt(16) | iv(12) | tag(16) | ciphertext
// header 里记录原文件名、明文大小、明文 sha256、算法与时间，恢复时可先验哈希再用。
//
// 用法：
//   node tools/vault.mjs keygen --key <keyfile>
//   node tools/vault.mjs seal   --key <keyfile> --in <file> --out <file.enc>
//   node tools/vault.mjs unseal --key <keyfile> --in <file.enc> --out <file>
//   node tools/vault.mjs info   --in <file.enc>
import { createCipheriv, createDecipheriv, randomBytes, scryptSync, createHash } from "node:crypto";
import { readFileSync, writeFileSync, existsSync, mkdirSync, statSync } from "node:fs";
import path from "node:path";

const MAGIC = Buffer.from("SMV1", "utf8");

function args(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i].startsWith("--")) {
      out[argv[i].slice(2)] = argv[i + 1] && !argv[i + 1].startsWith("--") ? argv[++i] : true;
    } else {
      out._ = argv[i];
    }
  }
  return out;
}

function need(opts, keys) {
  for (const key of keys) {
    if (!opts[key]) {
      console.error(`PROBLEM: missing --${key}`);
      process.exit(2);
    }
  }
}

function keyFrom(keyPath, salt) {
  const material = readFileSync(keyPath, "utf8").trim();
  if (material.length < 32) throw new Error("key material too short");
  return scryptSync(material, salt, 32, { N: 16384, r: 8, p: 1 });
}

function sha256(buffer) {
  return createHash("sha256").update(buffer).digest("hex");
}

function seal(keyPath, inPath, outPath) {
  const plain = readFileSync(inPath);
  const header = {
    name: path.basename(inPath),
    size: plain.length,
    sha256: sha256(plain),
    alg: "aes-256-gcm+scrypt",
    created_at: new Date().toISOString(),
  };
  const headerBuf = Buffer.from(JSON.stringify(header), "utf8");
  const salt = randomBytes(16);
  const iv = randomBytes(12);
  const cipher = createCipheriv("aes-256-gcm", keyFrom(keyPath, salt), iv);
  const body = Buffer.concat([cipher.update(plain), cipher.final()]);
  const tag = cipher.getAuthTag();
  const len = Buffer.alloc(4);
  len.writeUInt32BE(headerBuf.length, 0);
  mkdirSync(path.dirname(outPath), { recursive: true });
  writeFileSync(outPath, Buffer.concat([MAGIC, len, headerBuf, salt, iv, tag, body]));
  return { source: inPath, source_sha256: header.sha256, sealed: outPath,
           sealed_sha256: sha256(readFileSync(outPath)), sealed_bytes: statSync(outPath).size };
}

function parse(buf) {
  if (buf.slice(0, 4).toString() !== "SMV1") throw new Error("not a vault file");
  const headerLen = buf.readUInt32BE(4);
  const header = JSON.parse(buf.slice(8, 8 + headerLen).toString("utf8"));
  let at = 8 + headerLen;
  const salt = buf.slice(at, at + 16); at += 16;
  const iv = buf.slice(at, at + 12); at += 12;
  const tag = buf.slice(at, at + 16); at += 16;
  return { header, salt, iv, tag, body: buf.slice(at) };
}

function unseal(keyPath, inPath, outPath) {
  const { header, salt, iv, tag, body } = parse(readFileSync(inPath));
  const decipher = createDecipheriv("aes-256-gcm", keyFrom(keyPath, salt), iv);
  decipher.setAuthTag(tag);
  const plain = Buffer.concat([decipher.update(body), decipher.final()]);
  const got = sha256(plain);
  if (got !== header.sha256) throw new Error("sha256 mismatch after decrypt");
  mkdirSync(path.dirname(outPath), { recursive: true });
  writeFileSync(outPath, plain);
  return { restored: outPath, bytes: plain.length, sha256: got, matches: got === header.sha256,
           original_name: header.name, sealed_at: header.created_at };
}

function main() {
  const [cmd, ...rest] = process.argv.slice(2);
  const opts = args(rest);
  if (cmd === "keygen") {
    need(opts, ["key"]);
    if (existsSync(opts.key)) {
      console.error("PROBLEM: key file already exists, refusing to overwrite");
      process.exit(3);
    }
    mkdirSync(path.dirname(opts.key), { recursive: true });
    writeFileSync(opts.key, randomBytes(32).toString("hex") + "\n", { mode: 0o600 });
    console.log(JSON.stringify({ key: opts.key, bytes: 65, note: "keep this file off the vault volume" }));
    return 0;
  }
  if (cmd === "seal") {
    need(opts, ["key", "in", "out"]);
    console.log(JSON.stringify(seal(opts.key, opts.in, opts.out)));
    return 0;
  }
  if (cmd === "unseal") {
    need(opts, ["key", "in", "out"]);
    console.log(JSON.stringify(unseal(opts.key, opts.in, opts.out)));
    return 0;
  }
  if (cmd === "info") {
    need(opts, ["in"]);
    const { header } = parse(readFileSync(opts.in));
    console.log(JSON.stringify(header, null, 2));
    return 0;
  }
  console.error("usage: vault.mjs keygen|seal|unseal|info ...");
  return 2;
}

try {
  process.exit(main());
} catch (error) {
  // GCM 校验失败、密钥不对、文件被改动，都走这里：明确失败，绝不输出半份明文。
  console.error(`FAILED: ${error.code || error.name}: ${error.message}`);
  process.exit(4);
}