const supplierSelect = document.getElementById("supplier");
const form = document.getElementById("classify-form");
const statusEl = document.getElementById("status");
const resultEl = document.getElementById("result");
const submitBtn = document.getElementById("submit-btn");

async function loadSuppliers() {
  try {
    const res = await fetch("/api/suppliers");
    const health = await fetch("/api/health").then(r => r.json());
    if (health.mock_mode) {
      document.getElementById("mock-banner").style.display = "block";
    }
    const suppliers = await res.json();
    supplierSelect.innerHTML = '<option value="">Select a supplier…</option>';
    suppliers.forEach(s => {
      const opt = document.createElement("option");
      opt.value = s.id;
      opt.textContent = s.name;
      opt.dataset.name = s.name;
      supplierSelect.appendChild(opt);
    });
  } catch (e) {
    supplierSelect.innerHTML = '<option value="">⚠️ Failed to load suppliers</option>';
  }
}
loadSuppliers();

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  resultEl.style.display = "none";
  const supplierOpt = supplierSelect.options[supplierSelect.selectedIndex];
  const fileInput = document.getElementById("file");

  if (!supplierOpt.value || !fileInput.files.length) return;

  const formData = new FormData();
  formData.append("supplier_id", supplierOpt.value);
  formData.append("supplier_name", supplierOpt.dataset.name);
  formData.append("file", fileInput.files[0]);

  submitBtn.disabled = true;
  submitBtn.textContent = "Classifying… (this can take a minute for large catalogs)";
  statusEl.textContent = "Uploading and extracting products…";

  try {
    const res = await fetch("/api/classify", { method: "POST", body: formData });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Classification failed");
    }
    const data = await res.json();
    statusEl.textContent = "";
    resultEl.style.display = "block";
    document.getElementById("result-summary").textContent =
      `${data.total} products found — ${data.classified_count} classified, ${data.unclassified_count} need review.`;
    document.getElementById("download-link").href = data.download_url;
  } catch (e) {
    statusEl.textContent = "❌ " + e.message;
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = "Classify Products";
  }
});
