(function () {
	const csrfInput = document.querySelector('[data-delete-csrf]');
	const csrfToken = csrfInput ? csrfInput.value : '';
	const buttons = document.querySelectorAll('[data-delete-file]');
	if (!buttons.length || !csrfToken) {
		return;
	}

	async function deleteFile(button) {
		const confirmText = button.dataset.deleteConfirm || 'Delete file?';
		if (!window.confirm(confirmText)) {
			return;
		}

		button.disabled = true;
		try {
			const response = await fetch(button.dataset.deleteUrl || '', {
				method: 'POST',
				headers: {
					'Content-Type': 'application/json',
					'X-CSRFToken': csrfToken,
					'X-Requested-With': 'XMLHttpRequest',
				},
				body: JSON.stringify({
					path: button.dataset.deletePath || '',
					filename: button.dataset.deleteFilename || '',
				}),
			});
			const payload = await response.json().catch(function () { return {}; });
			if (!response.ok || !payload.redirect) {
				throw new Error(payload.error || 'delete_failed');
			}
			window.location.assign(payload.redirect);
		} catch (_err) {
			button.disabled = false;
			window.alert('Delete failed. Please refresh and try again.');
		}
	}

	buttons.forEach(function (button) {
		button.addEventListener('click', function () {
			deleteFile(button);
		});
	});
})();
