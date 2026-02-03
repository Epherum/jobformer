// Cleanup helper for the "Jobs_Today" tab.
//
// Goal: remove rows whose Title matches one of your negative terms.
//
// Safer workflow:
// - Start with dryRun=true to preview matches in Logs.
// - Then set dryRun=false to actually delete.
//
// Columns in Jobs_Today (from your sheet export):
// A source
// B labels
// C title
// D company
// E location
// F date_added
// G url
// H decision
// I notes

const JOBS_TODAY_TAB = 'Jobs_Today';
const JOBS_TODAY_COL_TITLE = 3;

// Edit this list freely. These are case-insensitive regex patterns.
const NEGATIVE_TITLE_PATTERNS = [
  // Seniority/leadership (broad)
  '\\bexecutive\\b',
  '\\bdirector\\b',
  '\\bdirecteur\\b',
  '\\bdirectrice\\b',
  '\\bvp\\b',
  '\\bvice\\s+president\\b',
  '\\bhead\\s+of\\b',
  '\\bchief\\b',
  '\\bc\\-level\\b',
  '\\bprincipal\\b',
  '\\bstaff\\b',
  '\\blead\\b',
  '\\bsenior\\b',
  '\\bsr\\b',
  '\\bconfirmé\\b',
  '\\bconfirmée\\b',
  // Very broad. Keep here for sheet cleanup. Might be too aggressive for scraper.
  '\\bmanager\\b',
  '\\barchitect\\b',

  // Sales-heavy pipeline roles
  'sales\\s+development\\s+representative',
  'business\\s+development\\s+representative',
  '\\bsdr\\b',
  '\\bbdr\\b',
  'télévente',
  'télévendeur',
  'télévendeurs',
  'televente',
  'televendeur',
  'televendeurs',

  // Support roles
  'customer\\s+care',
  'customer\\s+support',
  'service\\s+client',
  '\\bit\\s+support\\b',
  '\\bhelp\\s*desk\\b',

  // Retail / cashier / service / logistics
  '\\bcaissier\\b',
  '\\bcaisse\\b',
  '\\bcashier\\b',
  '\\bvendeur\\b',
  '\\bvendeuse\\b',
  '\\blivreur\\b',
  '\\bcoursier\\b',
  '\\bchauffeur\\b',
  '\\bpréparateur\\b',
  '\\bpreparateur\\b',

  // Non-software engineering / electrical
  'électricit',
  'electricit',
  'electri(?:c|que)',
  '\\bcfo\\b',
  '\\bcfa\\b',
  'génie\\s+civil',
  'genie\\s+civil',
  'revit',
  'coffrage',
  'ferraillage',

  // Manufacturing/industrial/quality (broad)
  'manufactur',
  'industrialisation',
  'maintenance\\s+industrielle',
  'maintenance',
  'automatisme',
  'assemblage',
  'contrôleur\\s+qualité',
  'controleur\\s+qualite',
  '\\bqualité\\b',
  '\\bqualite\\b',

  // QA/testing
  '\\bqa\\b',
  'test(\\b|eur|euse)',
  'fonctionnel(?:le)?',

  // Accounting/HR/marketing/product/video
  'comptab',
  'finance\\b',
  'ressources\\s+humaines',
  '\\brh\\b',
  'marketing\\b',
  'chef\\s+de\\s+produit',
  'product\\s+manager',
  'video\\s+editor',
  'monteur\\s+vid(?:é|e)o',
];

function purgeJobsTodayNotAFitByTitle() {
  const dryRun = true; // flip to false to delete

  const re = new RegExp(NEGATIVE_TITLE_PATTERNS.map(p => `(?:${p})`).join('|'), 'i');

  const ss = SpreadsheetApp.getActiveSpreadsheet();
  const sh = ss.getSheetByName(JOBS_TODAY_TAB);
  if (!sh) throw new Error(`Missing tab: ${JOBS_TODAY_TAB}`);

  const lastRow = sh.getLastRow();
  const lastCol = sh.getLastColumn();
  if (lastRow < 2) {
    Logger.log('No rows to process.');
    return;
  }

  const values = sh.getRange(1, 1, lastRow, lastCol).getValues();

  // Collect matching row indices (1-indexed sheet rows), skip header.
  const matches = [];
  for (let r = 2; r <= lastRow; r++) {
    const title = String(values[r - 1][JOBS_TODAY_COL_TITLE - 1] || '').trim();
    if (!title) continue;
    if (re.test(title)) {
      matches.push({ row: r, title });
    }
  }

  Logger.log(`Matches: ${matches.length}`);
  matches.slice(0, 50).forEach(m => Logger.log(`#${m.row}: ${m.title}`));
  if (matches.length > 50) Logger.log('… (more omitted)');

  if (dryRun) {
    Logger.log('Dry run ON. Set dryRun=false to delete these rows.');
    return;
  }

  // Delete from bottom to top so row numbers stay valid.
  matches.sort((a, b) => b.row - a.row);
  matches.forEach(m => sh.deleteRow(m.row));

  Logger.log(`Deleted ${matches.length} rows.`);
}
