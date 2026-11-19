/* Buildertrend pull, the change-orders step. Part of every pull since 2026-11-19.

   Run it in the signed-in browser on https://buildertrend.net/robots.txt (same origin, loads at once), with
   scripts/bt_receive_to_server.py listening on 127.0.0.1:8766. It reads Buildertrend's Change Orders list
   (Project Management > Change Orders) for every job, each change order's own page (its lines and the sentence
   "Approved by ... on ..."), and the purchase orders the change orders are billed through: a purchase-order
   line names its change order, and a bill line carries the same purchase-order line id. It then posts
   changeorders.json to the receiver (Buildertrend.import_change_orders -> bt_change_orders).

   Read only on Buildertrend's side. The last expression is a short count to check before trusting the load;
   nothing is posted unless every change order and every purchase order was read. */
const HJ = {Accept: 'application/json', 'Content-Type': 'application/json', PortalType: '1'};
const H = {Accept: 'application/json', PortalType: '1'};
const ANY_DATE = '{"SelectedValue":2147483647,"StartDate":null,"EndDate":null}';
const G = {gridRequest: {hideMultiJobsColumns: false, selectedColumns: ['13', '2', '0', '31', '27', '14', '3', '15', '5', '39', '6', '8', '19'], sortColumn: '3', sortDirection: 'desc', emptyStateEntity: 4, savedViewId: -1},
           filters: {'0': '', '3': '', '4': '', '10': '', '11': ANY_DATE, '13': ANY_DATE, '14': '0'}};
// Every job: the list the bills step keeps in the tab (mt_jobs), else the jobs known on 2026-11-19.
const JOBS = JSON.parse(sessionStorage.getItem('mt_jobs') || 'null') || [45092098, 45828952, 39879057, 43753419, 34404053, 37317241, 41154248, 43559663, 46366870, 46709825, 43665108, 46105577, 45775207, 43987126, 45008593, 40843754, 27766804, 38598052, 34545321, 40498775, 27766260, 45982984, 37186445];

let rows = [], err = null;
for (let p = 1; p <= 40; p++) {
  const b = {...G, jobIds: JOBS, pagingData: {pageNumber: String(p), pageSize: 50, resetScroll: false, firstRow: (p - 1) * 50 + 1, lastRow: p * 50, totalRowsAllPages: 50, currentPage: p}};
  const r = await fetch('/api/ChangeOrders/Grid', {method: 'POST', headers: HJ, body: JSON.stringify(b), credentials: 'include'});
  const j = await r.json();
  if (!(j.data && j.data.data)) { err = 'list: ' + r.status; break; }
  rows = rows.concat(j.data.data);
  if (j.data.data.length < 50 || p >= (j.data.totalPages || 1)) break;
}
const seen = new Set();
rows = rows.filter(r => !seen.has(r.id) && seen.add(r.id));

const mv = v => (v && typeof v === 'object' && 'value' in v) ? v.value : v;
const strip = s => String(s || '').replace(/<[^>]+>/g, ' ').replace(/&nbsp;/g, ' ').replace(/\s+/g, ' ').trim();
const cos = {}, bad = [];
let idx = 0;
async function worker() {
  while (idx < rows.length) {
    const r = rows[idx++];
    try {
      const rr = await fetch('/api/ChangeOrders/' + r.id + '/changeOrder', {headers: H, credentials: 'include'});
      if (!rr.ok) { bad.push(r.id + ':' + rr.status); continue; }
      const j = await rr.json();
      const d = j.data || j, ad = d.approvalDetails || {}, as = r.approvalStatus || {}, lv = (d.lineItems && d.lineItems.value) || [];
      cos[r.id] = {
        id: r.id, jobId: r.jobId, number: (r.idLink || {}).title, title: r.title, status: as.message, rawStatus: r.rawStatus, statusText: ad.statusText,
        statusAt: as.dateUpdated, statusBy: as.updatedBy, approvedBy: d.approvedBy, dateApproved: r.dateApproved, added: r.added, createdBy: d.createdBy,
        deadline: r.deadline, client: r.ownerName, clientViewed: d.viewedByOwnerOn, builderCost: mv(r.builderCost), ownerPrice: mv(r.totalWithTax),
        description: strip(mv(d.description)), notes: strip(mv(d.notes)),
        poIds: (((r.purchaseOrderLinks || {}).relatedPurchaseOrders) || []).map(p => p.id),
        files: ((d.attachedFilesPreApproval || {}).files || []).length + ((d.attachedFilesPostApproval || {}).files || []).length,
        lines: lv.map(l => ({id: l.id, title: l.title, description: strip(l.description), costCode: l.costCodeTitle || l.costCode, unitCost: l.unitCost, quantity: l.quantity, ownerPrice: l.ownerPrice})),
      };
    } catch (e) { bad.push(r.id + ':' + String(e).slice(0, 40)); }
  }
}
await Promise.all([1, 2, 3, 4, 5, 6].map(worker));

// The purchase orders: each line that names a change order (relatedItems, type 1) is the tie to the bills.
const poIds = [...new Set(Object.values(cos).flatMap(c => c.poIds))];
const poLines = [], poBad = [];
let pi = 0;
async function pworker() {
  while (pi < poIds.length) {
    const id = poIds[pi++];
    try {
      const rr = await fetch('/api/PurchaseOrders/' + id, {headers: H, credentials: 'include'});
      if (!rr.ok) { poBad.push(id + ':' + rr.status); continue; }
      const j = await rr.json();
      const d = j.data || j, lv = (d.lineItems && (d.lineItems.value || d.lineItems)) || [];
      for (const l of lv) for (const ri of (l.relatedItems || [])) if (ri.type === 1 && cos[ri.id]) poLines.push({poLineId: l.lineItemId, poId: id, coId: ri.id, title: l.title, unitCost: l.unitCost, quantity: l.quantity});
    } catch (e) { poBad.push(id + ':' + String(e).slice(0, 40)); }
  }
}
await Promise.all([1, 2, 3, 4, 5, 6].map(pworker));

const data = JSON.stringify({pulled_at: new Date().toISOString(), job_ids: JOBS, change_orders: Object.values(cos), po_lines: poLines});
const f = document.createElement('form');
f.method = 'POST';
f.action = 'http://127.0.0.1:8766/form';
const add = (k, v) => { const i = document.createElement('input'); i.type = 'hidden'; i.name = k; i.value = v; f.appendChild(i); };
add('name', 'changeorders.json'); add('kind', 'text'); add('data', data);
(document.body || document.documentElement).appendChild(f);
const ok = !err && bad.length === 0 && poBad.length === 0 && rows.length > 0 && Object.keys(cos).length === rows.length;
if (ok) setTimeout(() => f.submit(), 300);
const byStatus = {};
for (const c of Object.values(cos)) byStatus[c.status] = (byStatus[c.status] || 0) + 1;
JSON.stringify({err, jobs: JOBS.length, rows: rows.length, got: Object.keys(cos).length, bad, purchaseOrders: poIds.length, poBad, tiedLines: poLines.length, tiedChangeOrders: new Set(poLines.map(p => p.coId)).size, byStatus, size: data.length, posted: ok})
