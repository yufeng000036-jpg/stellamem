"""Read legacy records and raw evidence, import into an explicitly separate v2 store."""
import argparse,collections,gzip,hashlib,json,re,sys,uuid
from pathlib import Path
from store import Store,Principal,Invalid,dump,now,stamp
def run(source,data):
 source=Path(source).resolve();data=Path(data).resolve()
 if source==data or source in data.parents:raise ValueError("Separate destination required")
 paths=[source/x for x in ["core/preferences.md","long-term/projects.md","long-term/decisions.md","long-term/lessons.md","semantic/facts.md","semantic/profile.md","system/memory-inbox.md"]]
 before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths if p.exists()}
 candidates=[]
 for p in paths:
  if not p.exists():continue
  for match in re.finditer(r"^```stellamem\r?\n([^\r\n]+)\r?\n```",p.read_text(encoding="utf-8-sig"),re.M):
   c=json.loads(match[1]);candidates.append((str(p.relative_to(source)),c))
 wanted={c.get("provenance",{}).get("record_id") for _,c in candidates if c.get("status")=="active"}-{None}
 manifest={};raw={}
 mf=source/"_raw/MANIFEST.jsonl"
 if mf.exists():
  with mf.open(encoding="utf-8") as f:
   for line in f:
    item=json.loads(line)
    if item.get("record_id") in wanted:manifest[item["record_id"]]=item
  for name in {x["session_file"] for x in manifest.values()}:
   path=(source/"_raw/sessions"/(name.replace(".jsonl","")+".jsonl.gz")).resolve()
   if not path.is_relative_to((source/"_raw/sessions").resolve()):raise ValueError("Invalid archive reference")
   with gzip.open(path,"rt",encoding="utf-8") as f:
    for line in f:
     r=json.loads(line)
     if r.get("record_id") in wanted:raw[r["record_id"]]=r
 s=Store(data/"memory.sqlite3");p=Principal();counts=collections.Counter();groups=collections.defaultdict(list)
 for rel,c in candidates:
  group=c.get("claim_id") or "unidentified-"+hashlib.sha256(dump(c).encode()).hexdigest()
  # Inbox candidates are kept separate from approved records until reviewed.
  group=("inbox:" if rel.startswith("system/") else "canonical:")+group
  groups[group].append((rel,c))
 with s.transaction():
  for group,rows in groups.items():
   rows.sort(key=lambda x:(str(x[1].get("observed_at","")),str(x[1].get("revision_id",""))))
   origin=str(source)+"|"+group
   if s.db.execute("SELECT 1 FROM imports WHERE origin=?",(origin,)).fetchone():counts["already_imported"]+=1;continue
   current=None;allhash=hashlib.sha256(dump(rows).encode()).hexdigest()
   for rel,c in rows:
    prov=c.get("provenance") or {};record=raw.get(prov.get("record_id"))
    verified=bool(record and c.get("source")==record.get("role") and c.get("evidence") and c["evidence"] in record.get("text","") and hashlib.sha256(record.get("text","").encode()).hexdigest()==prov.get("content_hash"))
    state="active" if c.get("status")=="active" and verified else ("quarantined" if c.get("status")=="quarantined" else "pending")
    # All imported production knowledge stays owner-only regardless of legacy sensitivity.
    src={"reference":"legacy:"+rel+"#"+str(c.get("revision_id",group)),"evidence":str(c.get("evidence","")),"evidence_verified":verified,"original_role":c.get("source","unknown"),"provenance":prov,"legacy_claim_id":c.get("claim_id"),"legacy_revision_id":c.get("revision_id"),"legacy_sensitivity":c.get("sensitivity"),"imported_at":now()}
    value=str(c.get("value","")).strip()
    if not value:counts["empty_skipped"]+=1;continue
    kind=c.get("kind","fact");kind="identity" if kind=="user-core" else kind
    if kind not in {"fact","preference","project","decision","lesson","procedure","relationship","identity","event","commitment","reflection"}:kind="fact"
    try:
     occurred=stamp(prov.get("timestamp"),now())
    except Invalid:occurred=now()
    item={"title":value[:100],"content":value,"kind":kind,"scope":"owner","source":src,"tags":[str(c.get("subject",""))[:60]] if c.get("subject") else [],"occurred_at":occurred,"valid_from":occurred}
    try:
     if current is None:
      current=s._insert(p,item,ident=str(uuid.uuid5(uuid.NAMESPACE_URL,origin)),status=state)
     else:
      updated=s.validate(p,item,current);updated["status"]=state;current=s._write(p,updated,"legacy_version")
     counts["versions_imported"]+=1;counts["verified_versions"]+=verified
    except Invalid:counts["invalid_or_secret_skipped"]+=1
   if current:
    counts["memories_imported"]+=1
    s.db.execute("INSERT INTO imports VALUES(?,?,?)",(origin,current["id"],allhash))
 counts["final_status"]=s.stats(p)["by_status"]
 s.close()
 after={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths if p.exists()}
 report={"source_unchanged":before==after,"source_records":len(candidates),"source_groups":len(groups),"counts":dict(counts),"destination":str(data),"privacy":"all owner-only","original_file_hashes":before}
 if before!=after:report["warning"]="Source changed concurrently; inspect before further import"
 return report
if __name__=="__main__":
 ap=argparse.ArgumentParser();ap.add_argument("--source",required=True);ap.add_argument("--data",required=True);args=ap.parse_args()
 report=run(args.source,args.data)
 out=Path(__file__).resolve().parent/"reports/legacy-import.json";out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
 print(json.dumps({k:v for k,v in report.items() if k!="original_file_hashes"},ensure_ascii=True))
