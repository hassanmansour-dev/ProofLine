// Measured Ops inbox (ADR-0020): two small helpers. Every page works without them.
"use strict";

// "Revise with this feedback" needs text: disabled while its box is empty.
document.querySelectorAll("[data-needs-feedback]").forEach(function (button) {
  var box = document.getElementById(button.dataset.needsFeedback);
  if (!box) return;
  function sync() { button.disabled = box.value.trim() === ""; }
  box.addEventListener("input", sync);
  sync();
});

// Copy a block to the clipboard; if that isn't allowed, select it to copy by hand.
document.querySelectorAll("[data-copy]").forEach(function (button) {
  var label = button.textContent;
  button.addEventListener("click", function () {
    var box = document.getElementById(button.dataset.copy);
    if (!box) return;
    function selected() {
      box.focus();
      box.select();
      button.textContent = "Selected: copy it";
    }
    var done = function () { button.textContent = "Copied"; };
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(box.value).then(done, selected);
    } else {
      selected();
    }
    setTimeout(function () { button.textContent = label; }, 2500);
  });
});
