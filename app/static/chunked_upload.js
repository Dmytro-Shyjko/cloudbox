(function () {
	const uploadRoot = document.querySelector('[data-chunked-upload-root]');
	if (!uploadRoot) {
		return;
	}

	const uploadForm = uploadRoot.querySelector('[data-upload-form]');
	const fileInput = uploadRoot.querySelector('[data-upload-file]');
	const submitButton = uploadRoot.querySelector('[data-upload-submit]');
	const modal = document.getElementById('chunked-upload-modal');
	if (!uploadForm || !fileInput || !submitButton || !modal) {
		return;
	}

	const detailsFilename = modal.querySelector('[data-cu-filename]');
	const detailsSize = modal.querySelector('[data-cu-size]');
	const detailsProgress = modal.querySelector('[data-cu-progress]');
	const detailsChunks = modal.querySelector('[data-cu-chunks]');
	const detailsStatus = modal.querySelector('[data-cu-status]');
	const progressBar = modal.querySelector('[data-cu-progress-bar]');
	const pauseBtn = modal.querySelector('[data-cu-pause]');
	const resumeBtn = modal.querySelector('[data-cu-resume]');
	const cancelBtn = modal.querySelector('[data-cu-cancel]');
	const closeBtn = modal.querySelector('[data-cu-close]');

	const thresholdBytes = Number(uploadRoot.dataset.chunkedThresholdBytes || 20 * 1024 * 1024);
	const defaultChunkSize = 5 * 1024 * 1024;
	const userId = uploadRoot.dataset.userId || '0';
	const targetPath = uploadRoot.dataset.targetPath || '';
	const csrfToken = readCsrfToken();

	const state = {
		file: null,
		uploadId: null,
		fingerprint: null,
		totalChunks: 0,
		uploadedCount: 0,
		status: 'idle',
		controller: null,
		running: false,
	};

	function readCsrfToken() {
		const tokenInput = uploadForm.querySelector('input[name="csrf_token"]') || document.querySelector('input[name="csrf_token"]');
		if (tokenInput && tokenInput.value) {
			return tokenInput.value;
		}
		const meta = document.querySelector('meta[name="csrf-token"]');
		return meta ? meta.getAttribute('content') : '';
	}

	function buildFingerprint(file) {
		return `${file.name}|${file.size}|${file.lastModified}`;
	}

	function localStorageKey(file) {
		return `cb_upload_${userId}_${buildFingerprint(file)}`;
	}

	function formatBytes(bytes) {
		if (!Number.isFinite(bytes) || bytes <= 0) {
			return '0 B';
		}
		const units = ['B', 'KB', 'MB', 'GB'];
		let value = bytes;
		let idx = 0;
		while (value >= 1024 && idx < units.length - 1) {
			value /= 1024;
			idx += 1;
		}
		return idx === 0 ? `${Math.round(value)} ${units[idx]}` : `${value.toFixed(1)} ${units[idx]}`;
	}

	function setStatus(message) {
		detailsStatus.textContent = message;
	}

	function showModal() {
		modal.hidden = false;
	}

	function hideModal() {
		modal.hidden = true;
	}

	function setButtonsForStatus(status) {
		const isPaused = status === 'paused';
		const isDone = status === 'done';
		const isActive = status === 'uploading' || status === 'completing';
		pauseBtn.disabled = !isActive;
		resumeBtn.disabled = !(isPaused || status === 'error');
		cancelBtn.disabled = isDone;
	}

	function updateProgress(uploadedCount, totalChunks) {
		const safeTotal = totalChunks > 0 ? totalChunks : 1;
		const percent = Math.floor((uploadedCount / safeTotal) * 100);
		detailsProgress.textContent = `${percent}%`;
		detailsChunks.textContent = `${Math.min(uploadedCount, safeTotal)} / ${safeTotal}`;
		progressBar.style.width = `${percent}%`;
		state.uploadedCount = uploadedCount;
	}

	async function jsonRequest(url, options) {
		const opts = Object.assign({}, options || {});
		opts.headers = Object.assign({}, opts.headers || {});
		if (opts.method && opts.method.toUpperCase() === 'POST' && csrfToken) {
			opts.headers['X-CSRFToken'] = csrfToken;
		}
		const response = await fetch(url, opts);
		let payload = {};
		try {
			payload = await response.json();
		} catch (_err) {
			payload = {};
		}
		if (!response.ok) {
			const err = new Error(payload.error || `Request failed (${response.status})`);
			err.status = response.status;
			err.payload = payload;
			throw err;
		}
		return payload;
	}

	async function ensureUploadId(file) {
		const key = localStorageKey(file);
		const existingUploadId = localStorage.getItem(key);
		if (existingUploadId) {
			state.uploadId = existingUploadId;
			return existingUploadId;
		}
		const totalChunks = Math.ceil(file.size / defaultChunkSize);
		const payload = await jsonRequest('/api/uploads/init', {
			method: 'POST',
			headers: {
				'Content-Type': 'application/json'
			},
			body: JSON.stringify({
				filename: file.name,
				total_size: file.size,
				chunk_size: defaultChunkSize,
				total_chunks: totalChunks,
				target_path: targetPath,
			})
		});
		localStorage.setItem(key, payload.upload_id);
		state.uploadId = payload.upload_id;
		return payload.upload_id;
	}

	async function fetchStatus(uploadId) {
		return jsonRequest(`/api/uploads/${encodeURIComponent(uploadId)}/status`, {
			method: 'GET'
		});
	}

	async function uploadChunk(uploadId, chunkIndex, chunkBlob) {
		const response = await fetch(`/api/uploads/${encodeURIComponent(uploadId)}/chunk`, {
			method: 'POST',
			headers: {
				'Content-Type': 'application/octet-stream',
				'X-CSRFToken': csrfToken,
				'X-Chunk-Index': String(chunkIndex),
				'X-Chunk-Size': String(chunkBlob.size),
			},
			body: chunkBlob,
			signal: state.controller ? state.controller.signal : undefined,
		});
		let payload = {};
		try {
			payload = await response.json();
		} catch (_err) {
			payload = {};
		}
		if (!response.ok) {
			const err = new Error(payload.error || `Chunk upload failed (${response.status})`);
			err.status = response.status;
			err.payload = payload;
			throw err;
		}
		return payload;
	}

	async function completeUpload(uploadId) {
		return jsonRequest(`/api/uploads/${encodeURIComponent(uploadId)}/complete`, {
			method: 'POST',
			headers: {
				'Content-Type': 'application/json'
			},
			body: JSON.stringify({}),
		});
	}

	function markPaused() {
		state.status = 'paused';
		setButtonsForStatus('paused');
		setStatus('Upload paused. You can resume.');
	}

	function finishAndReload(message) {
		state.status = 'done';
		setButtonsForStatus('done');
		setStatus(message);
		setTimeout(function () {
			window.location.reload();
		}, 900);
	}

	async function runChunkedUpload(file) {
		if (state.running) {
			return;
		}
		state.running = true;
		state.file = file;
		state.fingerprint = buildFingerprint(file);
		state.totalChunks = Math.ceil(file.size / defaultChunkSize);
		detailsFilename.textContent = file.name;
		detailsSize.textContent = formatBytes(file.size);
		showModal();
		setStatus('Preparing upload...');
		setButtonsForStatus('uploading');

		try {
			const uploadId = await ensureUploadId(file);
			state.uploadId = uploadId;
			state.status = 'uploading';
			state.controller = new AbortController();

			const statusPayload = await fetchStatus(uploadId);
			const missingChunks = Array.isArray(statusPayload.missing_chunks) ? statusPayload.missing_chunks : [];
			updateProgress(state.totalChunks - missingChunks.length, state.totalChunks);
			setStatus('Uploading chunks...');

			for (const chunkIndex of missingChunks) {
				if (state.status !== 'uploading') {
					break;
				}
				const start = chunkIndex * defaultChunkSize;
				const end = Math.min(start + defaultChunkSize, file.size);
				const chunkBlob = file.slice(start, end);
				await uploadChunk(uploadId, chunkIndex, chunkBlob);
				updateProgress(state.uploadedCount + 1, state.totalChunks);
			}

			if (state.status !== 'uploading') {
				return;
			}

			setStatus('Completing upload...');
			setButtonsForStatus('completing');
			const completePayload = await completeUpload(uploadId);
			localStorage.removeItem(localStorageKey(file));
			if (completePayload.duplicate) {
				finishAndReload('File already exists, upload skipped.');
				return;
			}
			finishAndReload('Upload complete.');
		} catch (err) {
			if (err && err.name === 'AbortError') {
				markPaused();
				return;
			}
			state.status = 'error';
			setButtonsForStatus('error');
			if (err && err.status === 409) {
				setStatus('Chunk state mismatch. Press Resume to re-check status and continue.');
			} else {
				setStatus('Connection lost, you can resume.');
			}
		} finally {
			state.running = false;
		}
	}

	function pauseUpload() {
		if (state.controller) {
			state.controller.abort();
		}
	}

	function resumeUpload() {
		if (!state.file) {
			setStatus('Select the same file to resume upload.');
			return;
		}
		state.status = 'uploading';
		setButtonsForStatus('uploading');
		runChunkedUpload(state.file);
	}

	async function cancelUpload() {
		if (state.controller) {
			state.controller.abort();
		}
		if (state.uploadId) {
			try {
				await jsonRequest(`/api/uploads/${encodeURIComponent(state.uploadId)}/cancel`, {
					method: 'POST',
					headers: { 'Content-Type': 'application/json' },
					body: JSON.stringify({}),
				});
			} catch (_err) {
				// ignore cancel errors in UI flow
			}
		}
		if (state.file) {
			localStorage.removeItem(localStorageKey(state.file));
		}
		state.uploadId = null;
		state.file = null;
		state.status = 'idle';
		hideModal();
	}

	fileInput.addEventListener('change', function () {
		const file = fileInput.files && fileInput.files[0] ? fileInput.files[0] : null;
		if (!file) {
			return;
		}
		if (file.size <= thresholdBytes) {
			hideModal();
			return;
		}
		detailsFilename.textContent = file.name;
		detailsSize.textContent = formatBytes(file.size);
		updateProgress(0, Math.ceil(file.size / defaultChunkSize));
		setStatus('Ready to upload. Press upload to begin.');
		setButtonsForStatus('paused');
		resumeBtn.disabled = true;
	});

	uploadForm.addEventListener('submit', function (event) {
		const file = fileInput.files && fileInput.files[0] ? fileInput.files[0] : null;
		if (!file || file.size <= thresholdBytes) {
			return;
		}
		event.preventDefault();
		runChunkedUpload(file);
	});

	pauseBtn.addEventListener('click', pauseUpload);
	resumeBtn.addEventListener('click', resumeUpload);
	cancelBtn.addEventListener('click', cancelUpload);
	closeBtn.addEventListener('click', function () {
		if (state.status === 'done' || state.status === 'idle') {
			hideModal();
		}
	});
})();
