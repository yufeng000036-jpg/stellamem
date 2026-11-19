import {readFile} from "node:fs/promises";
import {createHash} from "node:crypto";
import {pathToFileURL} from "node:url";
import path from "node:path";

const [adapter, root, config] = process.argv.slice(2);
const {project} = await import(pathToFileURL(adapter));
const requests = JSON.parse(await readFile(config,"utf8"));
for (const {question, gold_id} of requests) {
  const output = await project(root, question, {requesterSubject:"user", activeScopes:[], maxInjectChars:3000, chineseTermSearch:true});
  const items = output.split("\n").slice(1).filter(Boolean).map(line=>JSON.parse(line));
  const hash = createHash("sha256").update(question).digest("hex").slice(0,16);
  const diag = JSON.parse(await readFile(path.join(process.env.STELLAMEM_PROJECT_DIAGNOSTICS_DIR,hash+".json"),"utf8"));
  const gold = diag.records.find(r=>r.claim_id===gold_id);
  console.log(JSON.stringify({items,stage:gold?.stage,reason:gold?.reason,permitted:diag.projected_n}));
}
