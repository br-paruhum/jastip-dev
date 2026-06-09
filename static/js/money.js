// Thousand-separator formatting for .money-input fields.
// Formats on blur (so the user sees e.g. 1,500.00 after leaving the field),
// removes separators on focus for easy editing, and strips them on submit as
// a safety net (the server-side widget also strips them).
(function () {
  function format(el) {
    var raw = el.value.replace(/,/g, '').trim();
    if (raw === '' || isNaN(raw)) return;
    var parts = raw.split('.');
    parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ',');
    el.value = parts.join('.');
  }
  function strip(el) { el.value = el.value.replace(/,/g, ''); }

  document.querySelectorAll('.money-input').forEach(function (el) {
    el.addEventListener('blur', function () { format(el); });
    el.addEventListener('focus', function () { strip(el); });
    if (el.value) format(el); // format any pre-filled value on load
  });

  document.querySelectorAll('form').forEach(function (form) {
    form.addEventListener('submit', function () {
      form.querySelectorAll('.money-input').forEach(strip);
    });
  });
})();
