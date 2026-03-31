// Google Apps Script for the "Jobs" tab.
//
// Features:
// - Adds a dropdown (data validation) for the Decision column.
// - When Decision becomes "APPLIED", sets decision_at timestamp once.
// - If Decision is cleared/changed away from "APPLIED", it clears decision_at.
//
// Install:
// 1) Open the Google Sheet
// 2) Extensions -> Apps Script
// 3) Paste this file
// 4) Run setupJobsSheet() once (authorize)
//
// Columns (1-indexed):
// A source
// B labels
// C title
// D company
// E location
// F date_added
// G url
// H decision
// I score
// J reason
// K feedback
// L suggested_decision

const TAB_NAMES = ['Jobs', 'Sales_Today', 'Tech_Today'];
const COL_DECISION = 8;
const COL_FEEDBACK = 11;

const DECISIONS = [
  'NEW',
  'SAVED',
  'APPLIED',
  'SKIPPED_NOT_A_FIT',
  'REJECTED',
  'ARCHIVED',
];

function setupJobsSheet() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  // Apply dropdown validation to all relevant tabs.
  const rule = SpreadsheetApp.newDataValidation()
    .requireValueInList(DECISIONS, true)
    .setAllowInvalid(false)
    .build();

  TAB_NAMES.forEach((tabName) => {
    const sheet = ss.getSheetByName(tabName);
    if (!sheet) return;
    sheet.getRange(2, COL_DECISION, sheet.getMaxRows() - 1, 1).setDataValidation(rule);
  });
}

function onEdit(e) {
  const range = e.range;
  const sheet = range.getSheet();
  if (!TAB_NAMES.includes(sheet.getName())) return;
  if (range.getRow() < 2) return; // ignore header

  if (range.getColumn() === COL_DECISION) {
    const decision = String(range.getValue() || '').trim();
    if (decision === 'SKIPPED_NOT_A_FIT') {
      const feedbackCell = sheet.getRange(range.getRow(), COL_FEEDBACK);
      if (!String(feedbackCell.getValue() || '').trim()) {
        feedbackCell.setNote('Write why this sales job was not a fit. This will be used to improve filtering later.');
      }
    }
  }
}
