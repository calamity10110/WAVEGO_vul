/* Shared tab navigation for all WAVEGO Pro pages.
   Include <script src="./nav.js"></script> anywhere in the page body —
   the header is injected automatically with the current tab highlighted. */
(function () {
    var PAGES = [
        { href: './', label: 'Control' },
        { href: './dashboard.html', label: 'Dashboard' },
        { href: './joystick.html', label: 'Joystick' },
        { href: './gait.html', label: 'Gait' },
        { href: './settings.html', label: 'Settings' },
        { href: './photo.html', label: 'Photos' },
        { href: './video.html', label: 'Videos' }
    ];

    var CSS = ':root{--nv-bg:#1a2129;--nv-panel:#101418;--nv-line:#2d3a47;--nv-txt:#d8e1ea;' +
        '--nv-dim:#8fa1b3;--nv-acc:#4fc3f7;color-scheme:dark;}' +
        '.nv-header{display:flex;align-items:center;gap:10px;flex-wrap:wrap;padding:10px 16px;' +
        'background:var(--nv-bg);border-bottom:1px solid var(--nv-line);}' +
        '.nv-brand{font-size:15px;font-weight:700;color:var(--nv-txt);text-decoration:none;}' +
        '.nv-tabs{display:flex;gap:4px;flex-wrap:wrap;}' +
        '.nv-tab{font-size:13px;color:var(--nv-dim);text-decoration:none;padding:6px 12px;' +
        'border-radius:6px;border:1px solid transparent;}' +
        '.nv-tab:hover{color:var(--nv-acc);border-color:var(--nv-line);}' +
        '.nv-tab:focus-visible{outline:2px solid var(--nv-acc);outline-offset:2px;}' +
        '.nv-tab.active{color:#bfe8ff;background:#0e3a52;border-color:var(--nv-acc);}' +
        '@media (prefers-reduced-motion: reduce){.nv-header *{transition:none !important;}}';

    function currentPage() {
        var path = location.pathname.split('/').pop();
        if (path === '' || path === 'index.html') { return './'; }
        return './' + path;
    }

    function build() {
        var style = document.createElement('style');
        style.textContent = CSS;
        document.head.appendChild(style);

        var header = document.createElement('header');
        header.className = 'nv-header';

        var brand = document.createElement('a');
        brand.className = 'nv-brand';
        brand.href = './';
        brand.textContent = 'WAVEGO Pro';
        header.appendChild(brand);

        var tabs = document.createElement('nav');
        tabs.className = 'nv-tabs';
        var here = currentPage();
        PAGES.forEach(function (p) {
            var a = document.createElement('a');
            a.className = 'nv-tab' + (p.href === here ? ' active' : '');
            a.href = p.href;
            if (p.href === here) { a.setAttribute('aria-current', 'page'); }
            a.textContent = p.label;
            tabs.appendChild(a);
        });
        header.appendChild(tabs);

        document.body.insertBefore(header, document.body.firstChild);
    }

    function setTitle() {
        var here = currentPage();
        var label = 'Control';
        PAGES.forEach(function (p) { if (p.href === here) { label = p.label; } });
        fetch('/config').then(function (r) { return r.text(); }).then(function (t) {
            var name = (window.jsyaml && jsyaml.load(t).base_config.robot_name) || 'WAVEGO Pro';
            document.title = name + ' \u2014 ' + label;
        }).catch(function () {
            document.title = 'WAVEGO Pro \u2014 ' + label;
        });
    }

    build();
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', setTitle);
    } else {
        setTitle();
    }
})();
