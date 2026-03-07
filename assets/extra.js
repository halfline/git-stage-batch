function getCurrentTopLevelLabel() {
	const activeTabLink = document.querySelector(".md-tabs__link--active");

	if (activeTabLink) {
		return activeTabLink.textContent.trim();
	}

	const currentPath = window.location.pathname;

	if (
		currentPath.endsWith("/") ||
		currentPath.endsWith("/index.html") ||
		currentPath.endsWith("/git-stage-batch/") ||
		currentPath.endsWith("/git-stage-batch/index.html")
	) {
		return "Home";
	}

	return null;
}

function updateSidebarVisibility() {
	const primarySidebar = document.querySelector(".md-sidebar--primary");

	if (!primarySidebar) {
		return;
	}

	const currentTopLevelLabel = getCurrentTopLevelLabel();
	const shouldHideSidebar =
		currentTopLevelLabel === "Home" ||
		currentTopLevelLabel === "Integration";

	if (shouldHideSidebar) {
		primarySidebar.style.setProperty("display", "none", "important");
	} else {
		primarySidebar.style.removeProperty("display");
	}
}

if (typeof document$ !== "undefined") {
	document$.subscribe(function () {
		updateSidebarVisibility();
	});
} else {
	document.addEventListener("DOMContentLoaded", function () {
		updateSidebarVisibility();
	});

	let lastUrl = location.href;

	setInterval(function () {
		if (location.href !== lastUrl) {
			lastUrl = location.href;
			updateSidebarVisibility();
		}
	}, 100);
}
