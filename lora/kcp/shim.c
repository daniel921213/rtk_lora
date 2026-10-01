#include "vendor/ikcp.h"

/* Conservative defaults for a slow, bidirectional radio link. */
void lora_kcp_config(ikcpcb *kcp, int rto_ms) {
    ikcp_setmtu(kcp, 234);
    ikcp_wndsize(kcp, 4, 128);
    ikcp_nodelay(kcp, 0, 50, 0, 0);
    kcp->rx_minrto = rto_ms;
    kcp->rx_rto = rto_ms;
}

int lora_kcp_failed(const ikcpcb *kcp) {
    return kcp->state == (IUINT32)-1;
}
