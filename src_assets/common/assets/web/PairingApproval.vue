<template>
  <form class="form w-100" @submit.prevent="submit">
    <div class="card p-4 mb-4">
      <h2>Approve a pairing request</h2>
      <p>Start pairing on your device, then refresh and select that exact request.
        Device names are supplied by the requester and are not verified. Check the source IP
        and certificate fingerprint; do not approve an unexpected or ambiguous request.</p>
      <button type="button" class="btn btn-secondary align-self-start mb-3"
              :disabled="loading || sending" @click="refresh">Refresh requests</button>
      <p v-if="loading">Loading pending requests...</p>
      <p v-else-if="!requests.length">No pending PIN requests. Start pairing on your device first.</p>
      <label v-for="request in requests" :key="request.token" class="border rounded p-3 mb-2">
        <input type="radio" name="pairing-request" :value="request.token" v-model="selectedToken"
               :disabled="sending" @change="resetPin" />
        <strong class="ms-2">{{ request.name || 'Unnamed device' }}</strong>
        <div>Source IP: <code>{{ request.source }}</code></div>
        <div>Certificate SHA-256: <code class="text-break">{{ request.fingerprint }}</code></div>
        <small>Expires in approximately {{ request.expires_in }} seconds from this refresh.</small>
      </label>
      <label for="pairing-pin" class="mt-3">PIN shown on the selected device</label>
      <input id="pairing-pin" class="form-control" v-model="pin" type="text" inputmode="numeric"
             pattern="[0-9]{4}" minlength="4" maxlength="4" autocomplete="off" required
             :disabled="!selectedToken || sending || loading" />
      <label for="pairing-name" class="mt-3">Optional device name</label>
      <input id="pairing-name" class="form-control mb-3" v-model="name" type="text" maxlength="256"
             :disabled="sending" />
      <button class="btn btn-primary" :disabled="!selectedToken || loading || sending">
        Submit PIN to selected request
      </button>
    </div>
    <p v-if="message" role="status" class="alert" :class="success ? 'alert-success' : 'alert-warning'">{{ message }}</p>
  </form>
</template>

<script>
export default {
  emits: ['submitted'],
  data() {
    return { requests: [], selectedToken: '', pin: '', name: '', loading: false,
      sending: false, message: '', success: false, controller: null, destroyed: false };
  },
  mounted() { this.refresh(); },
  beforeUnmount() {
    this.destroyed = true;
    this.controller?.abort();
    this.selectedToken = '';
    this.pin = '';
    this.requests = [];
  },
  methods: {
    resetPin() { this.pin = ''; this.message = ''; this.success = false; },
    async refresh() {
      if (this.sending || this.loading || this.destroyed) return;
      // A refresh never selects or silently retargets an approval.
      this.selectedToken = '';
      this.pin = '';
      this.requests = [];
      this.loading = true;
      this.controller = new AbortController();
      try {
        const response = await fetch('./api/pairing/requests', {
          credentials: 'include', cache: 'no-store', signal: this.controller.signal
        });
        if (!response.ok) throw new Error('Request list unavailable');
        const body = await response.json();
        if (body.status !== true || !Array.isArray(body.requests) || body.requests.some(request =>
          !request || typeof request.token !== 'string' || !/^[0-9a-fA-F]{64}$/.test(request.token) ||
          typeof request.name !== 'string' || typeof request.source !== 'string' ||
          typeof request.fingerprint !== 'string' || !Number.isFinite(request.expires_in))) {
          throw new Error('Invalid request list');
        }
        if (!this.destroyed) this.requests = body.requests;
      } catch (error) {
        if (!this.destroyed && error.name !== 'AbortError') {
          this.success = false;
          this.message = 'Could not load pending requests. Check your administrator session and refresh.';
        }
      } finally { this.loading = false; this.controller = null; }
    },
    async submit() {
      if (this.sending || this.loading || this.destroyed) return;
      const request = this.requests.find(item => item.token === this.selectedToken);
      if (!request || !/^[0-9]{4}$/.test(this.pin)) {
        this.success = false;
        this.message = 'Select a request and enter its four-digit PIN.';
        return;
      }
      // Snapshot the exact selection; never choose another request after failure.
      const payload = { token: request.token, pin: this.pin, name: this.name };
      this.sending = true;
      this.success = false;
      this.message = '';
      this.controller = new AbortController();
      try {
        const response = await fetch('./api/pin', {
          credentials: 'include', method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload), signal: this.controller.signal
        });
        if (!response.ok) throw new Error('Approval refused');
        const body = await response.json();
        if (body.status !== true) throw new Error('Request expired or already approved');
        if (!this.destroyed) {
          this.success = true;
          this.message = 'PIN submitted to the selected request. Complete pairing on your device and check its permissions.';
          this.$emit('submitted');
        }
      } catch (error) {
        if (!this.destroyed && error.name !== 'AbortError') {
          this.message = 'Approval failed or expired. Refresh and explicitly select a new request; no other request was approved automatically.';
        }
      } finally {
        this.sending = false;
        this.controller = null;
        this.selectedToken = '';
        this.pin = '';
        this.requests = [];
      }
    }
  }
};
</script>
