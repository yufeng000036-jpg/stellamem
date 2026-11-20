import { project } from "../../adapters/openclaw/index.js";
let question = "";
process.stdin.setEncoding("utf8");
for await (const chunk of process.stdin) question += chunk;
process.stdout.write(await project(process.argv[2], question, {chineseTermSearch: true, requesterSubject: "user", activeScopes: [], maxInjectChars: 3000}));
