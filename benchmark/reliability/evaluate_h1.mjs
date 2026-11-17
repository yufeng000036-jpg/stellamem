// Versioned H1 projection contract. The v1 corpus remains byte-for-byte frozen.
// Active assistant records project only after upstream approval; pending records never do.
// This adapter-level corpus does not prove that the approval path is secure.
import { readFile, mkdtemp, mkdir, writeFile, rm } from "node:fs/promises";
import { createHash } from "node:crypto";
import { pathToFileURL, fileURLToPath } from "node:url";
import os from "node:os";
import path from "node:path";
const here = path.dirname(fileURLToPath(import.meta.url));
const adapter = process.argv[2];
if (!adapter) throw new Error("Pass an explicit adapter path; no live host or default production root");
const { project } = await import(pathToFileURL(path.resolve(adapter)));
const payload = await readFile(path.join(here,"corpus.jsonl"),"utf8");
const manifest = JSON.parse(await readFile(path.join(here,"manifest.json"),"utf8"));
if (createHash("sha256").update(payload).digest("hex") !== manifest.sha256) throw new Error("frozen corpus changed");
const base = payload.trim().split("\n").map(JSON.parse);
const cases = structuredClone(base);
const assistantCases = cases.filter(item => item.kind === "functional" && item.family === 8);
if (assistantCases.length !== 20 || assistantCases.some(item =>
  item.records.length !== 1 || item.records[0].source !== "assistant" ||
  item.records[0].status !== "active" || item.expected_ids.length !== 0)) {
  throw new Error("v1 assistant family differs from the documented H1 migration");
}
for (const item of assistantCases) {
  item.expected_ids = [item.records[0].claim_id];
  const pending = structuredClone(item);
  pending.id = "H1-pending-" + item.id;
  pending.records[0].status = "pending";
  pending.expected_ids = [];
  cases.push(pending);
}
const results = [];
for (const item of cases) {
  const root = await mkdtemp(path.join(os.tmpdir(),"stellamem-contract-"));
  try {
    await mkdir(path.join(root,"core")); await mkdir(path.join(root,"system"));
    const rows = structuredClone(item.records);
    if (item.generated_secret) rows[0].value = "sk-" + "X".repeat(32);
    const render = rs => "<!-- stellamem:v2 -->\n" + rs.map(r=>"```stellamem\n"+JSON.stringify(r)+"\n```\n").join("\n");
    await writeFile(path.join(root,"core/preferences.md"),render(rows));
    if (item.conflict_inbox) await writeFile(path.join(root,"system/memory-inbox.md"),render([{...rows[0],status:"pending",relation:"CONTRADICTS"}]));
    if (item.corrupt_quarantine) await writeFile(path.join(root,"system/quarantine.json"),"{broken");
    const started = performance.now();
    const output = await project(root,item.prompt,item.config);
    const facts = output ? output.trim().split("\n").slice(1).map(JSON.parse) : [];
    const ids = facts.map(f=>f.id);
    const rank = item.expected_ids.length ? ids.indexOf(item.expected_ids[0])+1 : null;
    results.push({id:item.id,kind:item.kind,split:item.split,expected_ids:item.expected_ids,
      actual_ids:ids,rank,passed:JSON.stringify(ids)===JSON.stringify(item.expected_ids),
      unexpected:ids.filter(id=>!item.expected_ids.includes(id)).length,
      latency_ms:performance.now()-started});
  } finally { await rm(root,{recursive:true,force:true}); }
}
const scores = rows => {
  const positive=rows.filter(r=>r.expected_ids.length), n=positive.length;
  return {cases:rows.length,passed:rows.filter(r=>r.passed).length,
    positive_denominator:n,recall_at_1:n?positive.filter(r=>r.rank===1).length/n:null,
    recall_at_5:n?positive.filter(r=>r.rank>0&&r.rank<=5).length/n:null,
    mrr:n?positive.reduce((s,r)=>s+(r.rank>0?1/r.rank:0),0)/n:null,
    unexpected_projections:rows.reduce((s,r)=>s+r.unexpected,0)};
};
console.log(JSON.stringify({contract_version:"h1-v2", base_corpus_sha256:manifest.sha256, derived_cases:cases.length, adapter:path.resolve(adapter),
  warning:manifest.purpose + "; adapter contract only; approval is not validated",label_author:manifest.label_author,
  overall:scores(results),holdout:scores(results.filter(r=>r.split==="holdout")),
  adversarial:scores(results.filter(r=>r.kind==="adversarial")),
  extraction_quality:"NOT MEASURED",answer_evidence_support:"NOT MEASURED",
  source_link_completeness:"NOT MEASURED",results},null,2));
// A red contract must fail the CLI so CI cannot report success on a regression.
if (results.some(r => !r.passed)) process.exitCode = 1;
