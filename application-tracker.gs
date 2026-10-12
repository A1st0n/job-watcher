// Job application tracker: paste into a Google Sheet via Extensions > Apps Script, then run setup() once.
// Every hour it scans Gmail for application confirmations, rejections and interview/OA emails.

const SHEET = 'Applications';
const HEADER = ['Date', 'Company', 'Role', 'Status', 'Last update', 'Email', 'Thread ID'];
const APPLIED = '(subject:("thank you for applying" OR "thanks for applying" OR "application received" OR ' +
  '"we received your application" OR "your application to" OR "your application for" OR "application submitted") ' +
  'OR "thank you for your application" OR "we have received your application")';
const REJECTED = /not (be )?moving forward|other candidates|decided not to|unable to offer|not been selected|regret to inform|position has been filled/i;
const INTERVIEW = /interview|online assessment|coding challenge|hackerrank|codesignal|next steps|schedule a (call|time)|phone screen/i;
const SKIP = '-from:notify-noreply@google.com -subject:"[New Job]"';

function setup() {
  const ss = SpreadsheetApp.getActive();
  const sh = ss.getSheetByName(SHEET) || ss.insertSheet(SHEET);
  if (sh.getLastRow() === 0) sh.appendRow(HEADER);
  sh.setFrozenRows(1);
  sh.hideColumns(7);
  ScriptApp.getProjectTriggers().filter(t => t.getHandlerFunction() === 'scan').forEach(t => ScriptApp.deleteTrigger(t));
  ScriptApp.newTrigger('scan').timeBased().everyHours(1).create();
  scan('newer_than:60d');  // backfill the last two months
}

function scan(window) {
  window = typeof window === 'string' ? window : 'newer_than:2d';
  const sh = SpreadsheetApp.getActive().getSheetByName(SHEET);
  const rows = sh.getDataRange().getValues();
  const seen = new Set(rows.map(r => r[6]));

  GmailApp.search(`${window} ${APPLIED} ${SKIP}`, 0, 200).forEach(t => {
    if (seen.has(t.getId())) return;
    const m = t.getMessages()[0];
    const subj = m.getSubject();
    const body = m.getPlainBody().slice(0, 3000);
    sh.appendRow([m.getDate(), company(m.getFrom(), subj, body), role(subj, body), 'Applied', m.getDate(),
      `https://mail.google.com/mail/u/0/#all/${t.getId()}`, t.getId()]);
    seen.add(t.getId());
  });

  // Status updates: match later emails to an existing row by company name.
  const data = sh.getDataRange().getValues();
  GmailApp.search(`${window} -${APPLIED} ${SKIP} -in:sent`, 0, 200).forEach(t => {
    const m = t.getMessages().pop();
    const text = m.getSubject() + ' ' + m.getPlainBody().slice(0, 3000);
    const status = REJECTED.test(text) ? 'Rejected' : INTERVIEW.test(text) ? 'Interview/OA' : null;
    if (!status) return;
    for (let i = 1; i < data.length; i++) {
      const name = String(data[i][1]).toLowerCase();
      if (name.length > 2 && (m.getFrom() + ' ' + text).toLowerCase().includes(name) && data[i][3] !== 'Rejected') {
        sh.getRange(i + 1, 4, 1, 2).setValues([[status, m.getDate()]]);
      }
    }
  });
}

// ponytail: heuristics; sender name first, then "applying to X" in subject/body. Fix odd rows by hand.
function company(from, subj, body) {
  const m = (subj + ' ' + body).match(/(?:applying|application) (?:to|at|with) ([A-Z][\w&.\- ]{1,40}?)(?:[!.,]| for | -|\n|$)/);
  if (m) return m[1].trim();
  const name = from.replace(/<.*>/, '').replace(/"/g, '')
    .replace(/\b(recruiting|recruitment|careers?|talent( acquisition)?|hiring( team)?|team|jobs|no-?reply|notifications?)\b/gi, '').trim();
  return /greenhouse|lever|workday|ashby|icims|smartrecruiters|^$/i.test(name) ? '(check email)' : name;
}

function role(subj, body) {
  const m = (subj + '\n' + body).match(/(?:for|for the|position:|role:)\s+(?:the\s+)?([A-Z][^\n.!,]{3,80}?(?:Intern(ship)?|Engineer|Developer|Graduate|Co-?op)[^\n.!,]{0,30})/);
  return m ? m[1].trim() : '';
}
