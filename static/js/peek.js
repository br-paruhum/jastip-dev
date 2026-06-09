// Password reveal toggle.
document.querySelectorAll('.peek').forEach(function (btn) {
  btn.addEventListener('click', function () {
    var input = document.getElementById(btn.dataset.target);
    if (!input) return;
    var show = input.type === 'password';
    input.type = show ? 'text' : 'password';
    btn.textContent = show ? 'Hide' : 'Show';
  });
});
