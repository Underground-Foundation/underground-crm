// colorfield.js only initializes Coloris for inputs present at window.load.
// Wagtail's StreamField admin inserts blocks dynamically after that event, so
// those new color inputs never get their instance-specific options (swatches, etc.).
// This observer watches for new inputs and runs the same initialization.
function initColorisInput(input) {
    const colorisId = input.getAttribute('data-coloris-options-json-script-id');
    if (!colorisId) return;
    const script = document.getElementById(colorisId);
    if (!script) return;
    const options = JSON.parse(script.textContent);
    const id = input.getAttribute('id');
    if (id) {
        Coloris.setInstance(`.colorfield_field.coloris.${id}`, options);
    }
}

document.addEventListener('DOMContentLoaded', function () {
    new MutationObserver(function (mutations) {
        for (const mutation of mutations) {
            for (const node of mutation.addedNodes) {
                if (node.nodeType !== Node.ELEMENT_NODE) continue;
                if (node.matches('.colorfield_field.coloris')) {
                    initColorisInput(node);
                }
                node.querySelectorAll('.colorfield_field.coloris').forEach(initColorisInput);
            }
        }
    }).observe(document.body, {childList: true, subtree: true});
});
