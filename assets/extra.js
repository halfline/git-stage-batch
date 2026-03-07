function updateSidebarVisibility() {
	const primarySidebar = document.querySelector(".md-sidebar--primary");

	if (!primarySidebar) {
		return;
	}

	/*
	 * Find the active top-level nav item in the primary sidebar.
	 *
	 * Material marks active items with .md-nav__item--active.
	 * We want the top-level one, not a nested child page.
	 */
	const activeTopLevelItem = primarySidebar.querySelector(
		":scope .md-nav--primary > .md-nav__list > .md-nav__item--active"
	);

	if (!activeTopLevelItem) {
		primarySidebar.style.removeProperty("display");
		return;
	}

	/*
	 * Count child pages for the active top-level section.
	 *
	 * Cases:
	 *   - Home: no nested list at all
	 *   - Integration: one child page
	 *   - Getting Started / Reference: multiple child pages
	 */
	const childLinks = activeTopLevelItem.querySelectorAll(
		":scope > .md-nav > .md-nav__list > .md-nav__item"
	);

	const shouldHideSidebar = childLinks.length <= 1;

	if (shouldHideSidebar) {
		primarySidebar.style.setProperty("display", "none", "important");
	} else {
		primarySidebar.style.removeProperty("display");
	}
}

if (typeof document$ !== "undefined") {
	document$.subscribe(updateSidebarVisibility);
} else {
	document.addEventListener("DOMContentLoaded", updateSidebarVisibility);

	let lastUrl = location.href;

	setInterval(() => {
		if (location.href !== lastUrl) {
			lastUrl = location.href;
			updateSidebarVisibility();
		}
	}, 100);
}
