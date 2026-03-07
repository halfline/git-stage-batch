// Hide left sidebar for single-item sections (Home, Integration)
function updateSidebarVisibility() {
  const currentPath = window.location.pathname;

  // Check if we're on a page that should hide the sidebar
  const isHomePage = currentPath.endsWith('/') ||
                     currentPath.endsWith('/index.html') ||
                     currentPath.endsWith('/git-stage-batch/') ||
                     currentPath.endsWith('/git-stage-batch/index.html');

  const isIntegrationPage = currentPath.includes('ai-assistants');

  const shouldHideSidebar = isHomePage || isIntegrationPage;

  const primarySidebar = document.querySelector('.md-sidebar--primary');
  if (primarySidebar) {
    if (shouldHideSidebar) {
      primarySidebar.style.setProperty('display', 'none', 'important');
    } else {
      primarySidebar.style.setProperty('display', 'block', 'important');
    }
  }
}

// Hook into MkDocs Material's navigation system
if (typeof document$ !== 'undefined') {
  document$.subscribe(updateSidebarVisibility);
} else {
  // Fallback for initial load
  document.addEventListener('DOMContentLoaded', updateSidebarVisibility);

  // Watch for navigation changes
  let lastUrl = location.href;
  setInterval(() => {
    if (location.href !== lastUrl) {
      lastUrl = location.href;
      updateSidebarVisibility();
    }
  }, 100);
}
