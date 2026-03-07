function shouldHidePrimarySidebar() {
	const path = window.location.pathname.replace(/index\.html$/, "");

	return (
		path === "/" ||
		path.endsWith("/git-stage-batch/") ||
		path.endsWith("/ai-assistants/")
	);
}

function updateSidebarVisibility() {
	if (shouldHidePrimarySidebar()) {
		document.body.classList.add("hide-primary-sidebar");
	} else {
		document.body.classList.remove("hide-primary-sidebar");
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
}
