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

// Persistent audio player logic
var persistentAudio = null;

function initPersistentAudio() {
	const audioElement = document.querySelector('audio.podcast-player');

	if (audioElement && !persistentAudio) {
		// First time encountering the audio element - save it globally
		persistentAudio = audioElement;

		// Mark body as having audio
		document.body.classList.add('has-audio');

		// Set up event listeners
		persistentAudio.addEventListener('play', updateAudioButton);
		persistentAudio.addEventListener('pause', updateAudioButton);
		persistentAudio.addEventListener('ended', updateAudioButton);

		// Initial button state
		updateAudioButton();
	} else if (audioElement && persistentAudio && audioElement !== persistentAudio) {
		// Navigation happened - replace the new element with our persistent one
		audioElement.parentNode.replaceChild(persistentAudio, audioElement);
	} else if (!audioElement && persistentAudio) {
		// We're on a page without the audio element, but keep it alive
		document.body.classList.add('has-audio');
	}
}

function updateAudioButton() {
	if (!persistentAudio) return;

	const button = document.querySelector('.md-header__button.audio-control');
	if (!button) return;

	const playIcon = button.querySelector('.play-icon');
	const pauseIcon = button.querySelector('.pause-icon');

	if (persistentAudio.paused) {
		button.classList.remove('playing');
		playIcon.style.display = '';
		pauseIcon.style.display = 'none';
		button.setAttribute('aria-label', 'Play podcast');
	} else {
		button.classList.add('playing');
		playIcon.style.display = 'none';
		pauseIcon.style.display = '';
		button.setAttribute('aria-label', 'Pause podcast');
	}
}

if (typeof document$ !== "undefined") {
	document$.subscribe(function () {
		updateSidebarVisibility();
		initPersistentAudio();
	});
} else {
	document.addEventListener("DOMContentLoaded", function () {
		updateSidebarVisibility();
		initPersistentAudio();
	});
}
