// Experiment-only adapter copy. Abort if the insertion sites drift.
import {readFile, writeFile, mkdir, cp} from "node:fs/promises";
import {createHash} from "node:crypto";
import path from "node:path";
import {pathToFileURL,fileURLToPath} from "node:url";
const repo = fileURLToPath(new URL("../../", import.meta.url));
const [root, work] = process.argv.slice(2);
let input="";
process.stdin.setEncoding("utf8");
for await(const chunk of process.stdin) input+=chunk;
const {question, ranks} = JSON.parse(input);
const original=path.join(repo,"adapters/openclaw/index.js");
let modulePath=original;
const diag=path.join(work,"diag");
process.env.STELLAMEM_PROJECT_DIAGNOSTICS_DIR=diag;
{
  modulePath=path.join(work,"adapters/openclaw/index.js");
  let code=await readFile(original,"utf8");
  const inject=(needle, replacement)=>{
    if(code.split(needle).length!==2) throw Error("Unexpected adapter version");
    code=code.replace(needle,()=>replacement);
  };
  if (ranks !== null) {
  inject("if (gateFailed) {","if (gateFailed && !config.semanticRanks.has(c.revision_id)) {");
  inject("topical: r.lexical || r.bigram, always","topical: r.lexical || r.bigram || config.semanticRanks.has(c.revision_id), always");
  const sort="candidates.sort((a, b) => b.score - a.score);";
  inject(sort,sort+`
  let lexicalRank=0;
  for(const item of candidates) {
    // The unmodified relevance and gate decide membership of the lexical list.
    const lexical=item.entry.lexicalPassed ? 1/(60+(++lexicalRank)) : 0;
    const sr=config.semanticRanks.get(item.c.revision_id);
    item.score=item.always ? 2 : lexical + (sr ? 1/(60+sr) : 0);
  }
  candidates.sort((a,b)=>b.score-a.score);
  candidates.forEach((item,i)=>{item.entry.preRank=i+1;});`);
  inject("if (!always) {\n        let gateFailed", "let lexicalPassed = true;\n      if (!always) {\n        let gateFailed");
  inject("if (gateFailed && !config.semanticRanks", "lexicalPassed = !gateFailed;\n        if (gateFailed && !config.semanticRanks");
  inject("candidates.push({ c, score, entry,", "entry.lexicalPassed = lexicalPassed;\n      candidates.push({ c, score, entry,");
  } else {
    const sort="candidates.sort((a, b) => b.score - a.score);";
    inject(sort,sort+"\ncandidates.forEach((item,i)=>{item.entry.preRank=i+1;});");
  }
  await mkdir(path.dirname(modulePath),{recursive:true});
  await writeFile(modulePath,code);
  await cp(path.join(repo,"shared"),path.join(work,"shared"),{recursive:true});
  await writeFile(path.join(work,"package.json"),'{"type":"module"}');
}
const {project}=await import(pathToFileURL(modulePath));
const context=await project(root,question,{requesterSubject:"user",activeScopes:[],maxInjectChars:3000,chineseTermSearch:true,
  semanticRanks:new Map((ranks||[]).map((id,i)=>[id,i+1]))});
const key=createHash("sha256").update(question).digest("hex").slice(0,16);
const trace=JSON.parse(await readFile(path.join(diag,key+".json"),"utf8"));
console.log(JSON.stringify({context,records:trace.records,empty:trace.empty_reason}));
