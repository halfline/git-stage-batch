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

// Persistent audio player logic (window-level so header can access it)
window.persistentAudio = null;

window.togglePodcast = function() {
	if (!window.persistentAudio) return;

	if (window.persistentAudio.paused) {
		window.persistentAudio.play();
	} else {
		window.persistentAudio.pause();
	}
};

window.updateAudioButton = function() {
	if (!window.persistentAudio) return;

	const button = document.querySelector('.md-header__button.audio-control');
	if (!button) return;

	const playIcon = button.querySelector('.play-icon');
	const pauseIcon = button.querySelector('.pause-icon');

	if (window.persistentAudio.paused) {
		button.classList.remove('playing');
		playIcon.style.display = '';
		pauseIcon.style.display = 'none';
		button.setAttribute('aria-label', 'Play podcast');
		button.setAttribute('title', 'Play Deep Dive podcast');
	} else {
		button.classList.add('playing');
		playIcon.style.display = 'none';
		pauseIcon.style.display = '';
		button.setAttribute('aria-label', 'Pause podcast');
		button.setAttribute('title', 'Pause Deep Dive podcast');
	}
};

function initPersistentAudio() {
	const audioElement = document.querySelector('audio.podcast-player');

	if (audioElement && !window.persistentAudio) {
		// First time encountering the audio element - save it globally
		window.persistentAudio = audioElement;

		// Mark body as having audio
		document.body.classList.add('has-audio');

		// Set up event listeners for button state sync
		window.persistentAudio.addEventListener('play', window.updateAudioButton);
		window.persistentAudio.addEventListener('pause', window.updateAudioButton);
		window.persistentAudio.addEventListener('ended', window.updateAudioButton);

		// Initial button state
		window.updateAudioButton();
	} else if (audioElement && window.persistentAudio && audioElement !== window.persistentAudio) {
		// Navigation happened - replace the new element with our persistent one
		audioElement.parentNode.replaceChild(window.persistentAudio, audioElement);
	} else if (!audioElement && window.persistentAudio) {
		// We're on a page without the audio element, but keep it alive
		document.body.classList.add('has-audio');
	}
}

function initMobileGithubWidget() {
	var target = document.querySelector(".mobile-github-widget");
	if (!target) return;
	if (target.querySelector(".md-source")) return;

	var source = document.querySelector(".md-header__source .md-source");
	if (!source) return;

	target.appendChild(source.cloneNode(true));
}

if (typeof document$ !== "undefined") {
	document$.subscribe(function () {
		updateSidebarVisibility();
		initPersistentAudio();
		initMobileGithubWidget();
	});
} else {
	document.addEventListener("DOMContentLoaded", function () {
		updateSidebarVisibility();
		initPersistentAudio();
		initMobileGithubWidget();
	});
}
