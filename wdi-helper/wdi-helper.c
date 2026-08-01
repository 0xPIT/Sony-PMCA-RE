/*
 * wdi-helper: Install/remove libusb-win32 filter for Sony service-mode devices.
 * Built against libwdi (https://github.com/pbatard/libwdi).
 *
 * Exit codes:
 *   0  success
 *   1  device not found
 *   2  driver preparation failed
 *   3  driver installation failed
 *   4  restore failed
 *   5  bad arguments / usage
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <windows.h>

#include "libwdi.h"

#define EXIT_OK           0
#define EXIT_NOT_FOUND    1
#define EXIT_PREPARE_FAIL 2
#define EXIT_INSTALL_FAIL 3
#define EXIT_RESTORE_FAIL 4
#define EXIT_USAGE        5

#define DEFAULT_VID       0x054C
#define DEFAULT_NAME      "Sony Camera Service Mode"
#define INF_NAME          "sony_senser.inf"
#define STATE_FILE_NAME   "sony_pmca_driver_state.json"

static void usage(void)
{
	printf(
		"Usage:\n"
		"  wdi-helper install --vid 0x054C --pid 0x0336 [--name \"...\"]\n"
		"  wdi-helper restore --vid 0x054C --pid 0x0336\n"
		"  wdi-helper --help\n"
		"\n"
		"Installs or removes the libusb-win32 upper filter for a USB device.\n"
	);
}

static int parse_u16(const char *s, unsigned short *out)
{
	char *end = NULL;
	unsigned long v;

	if (s == NULL || out == NULL) {
		return -1;
	}
	v = strtoul(s, &end, 0);
	if (end == s || *end != '\0' || v > 0xFFFFUL) {
		return -1;
	}
	*out = (unsigned short)v;
	return 0;
}

static int state_path(char *buf, size_t buflen)
{
	DWORD n = GetTempPathA((DWORD)buflen, buf);
	size_t len;

	if (n == 0 || n >= buflen) {
		return -1;
	}
	len = strlen(buf);
	if (len + strlen(STATE_FILE_NAME) + 1 >= buflen) {
		return -1;
	}
	strcat_s(buf, buflen, STATE_FILE_NAME);
	return 0;
}

static void json_escape(const char *src, char *dst, size_t dstlen)
{
	size_t o = 0;
	if (dstlen == 0) {
		return;
	}
	if (src == NULL) {
		dst[0] = '\0';
		return;
	}
	while (*src && o + 2 < dstlen) {
		if (*src == '"' || *src == '\\') {
			if (o + 3 >= dstlen) {
				break;
			}
			dst[o++] = '\\';
			dst[o++] = *src++;
		} else if ((unsigned char)*src < 0x20) {
			src++;
		} else {
			dst[o++] = *src++;
		}
	}
	dst[o] = '\0';
}

static int write_state(unsigned short vid, unsigned short pid, const char *driver)
{
	char path[MAX_PATH];
	char escaped[WDI_MAX_STRLEN * 2];
	FILE *fp;

	if (state_path(path, sizeof(path)) != 0) {
		return -1;
	}
	json_escape(driver, escaped, sizeof(escaped));
	if (fopen_s(&fp, path, "wb") != 0 || fp == NULL) {
		return -1;
	}
	fprintf(fp,
		"{\"vid\":%u,\"pid\":%u,\"driver\":\"%s\"}\n",
		(unsigned int)vid, (unsigned int)pid, escaped);
	fclose(fp);
	return 0;
}

static void delete_state(void)
{
	char path[MAX_PATH];

	if (state_path(path, sizeof(path)) == 0) {
		DeleteFileA(path);
	}
}

/* Snapshot of the fields we need after wdi_destroy_list frees the list. */
struct device_snap {
	char hardware_id[WDI_MAX_STRLEN];
	char device_id[WDI_MAX_STRLEN];
	char driver[WDI_MAX_STRLEN];
	char upper_filter[WDI_MAX_STRLEN];
	BOOL is_composite;
	unsigned char mi;
	BOOL found;
};

static void copy_str(char *dst, size_t dstlen, const char *src)
{
	if (dstlen == 0) {
		return;
	}
	if (src == NULL) {
		dst[0] = '\0';
		return;
	}
	strncpy_s(dst, dstlen, src, _TRUNCATE);
}

static struct device_snap lookup_device(unsigned short vid, unsigned short pid)
{
	struct device_snap snap;
	struct wdi_device_info *list = NULL;
	struct wdi_device_info *dev;
	struct wdi_options_create_list ocl = { 0 };
	int r;

	memset(&snap, 0, sizeof(snap));
	ocl.list_all = TRUE;
	ocl.trim_whitespaces = TRUE;

	r = wdi_create_list(&list, &ocl);
	if (r != WDI_SUCCESS) {
		fprintf(stderr, "wdi_create_list failed: %s\n", wdi_strerror(r));
		return snap;
	}

	for (dev = list; dev != NULL; dev = dev->next) {
		if (dev->vid == vid && dev->pid == pid) {
			copy_str(snap.hardware_id, sizeof(snap.hardware_id), dev->hardware_id);
			copy_str(snap.device_id, sizeof(snap.device_id), dev->device_id);
			copy_str(snap.driver, sizeof(snap.driver), dev->driver);
			copy_str(snap.upper_filter, sizeof(snap.upper_filter), dev->upper_filter);
			snap.is_composite = dev->is_composite;
			snap.mi = dev->mi;
			snap.found = TRUE;
			break;
		}
	}

	wdi_destroy_list(list);
	return snap;
}

static int cmd_install(unsigned short vid, unsigned short pid, const char *name)
{
	struct device_snap snap;
	struct wdi_device_info dev;
	struct wdi_options_prepare_driver opd = { 0 };
	struct wdi_options_install_driver oid = { 0 };
	char temp_dir[MAX_PATH];
	char extract_dir[MAX_PATH];
	DWORD n;
	int r;

	snap = lookup_device(vid, pid);
	if (!snap.found) {
		fprintf(stderr, "Device %04X:%04X not found.\n",
			(unsigned int)vid, (unsigned int)pid);
		return EXIT_NOT_FOUND;
	}

	memset(&dev, 0, sizeof(dev));
	dev.vid = vid;
	dev.pid = pid;
	dev.desc = (char *)((name != NULL && name[0] != '\0') ? name : DEFAULT_NAME);
	dev.hardware_id = snap.hardware_id[0] ? snap.hardware_id : NULL;
	dev.device_id = snap.device_id[0] ? snap.device_id : NULL;
	dev.is_composite = snap.is_composite;
	dev.mi = snap.mi;

	printf("Found device: %s\n",
		snap.hardware_id[0] ? snap.hardware_id : "(no hardware id)");
	printf("Current driver: %s\n",
		snap.driver[0] ? snap.driver : "(none)");
	printf("Upper filter: %s\n",
		snap.upper_filter[0] ? snap.upper_filter : "(none)");

	if (write_state(vid, pid, snap.driver) != 0) {
		fprintf(stderr, "Warning: could not write driver state file.\n");
	}

	n = GetTempPathA((DWORD)sizeof(temp_dir), temp_dir);
	if (n == 0 || n >= sizeof(temp_dir)) {
		fprintf(stderr, "GetTempPath failed.\n");
		return EXIT_PREPARE_FAIL;
	}
	sprintf_s(extract_dir, sizeof(extract_dir), "%swdi_helper_%04X_%04X",
		temp_dir, (unsigned int)vid, (unsigned int)pid);
	CreateDirectoryA(extract_dir, NULL);

	opd.driver_type = WDI_LIBUSB0;
	opd.vendor_name = "Sony";

	printf("Preparing libusb-win32 filter driver...\n");
	r = wdi_prepare_driver(&dev, extract_dir, INF_NAME, &opd);
	if (r != WDI_SUCCESS) {
		fprintf(stderr, "wdi_prepare_driver failed: %s\n", wdi_strerror(r));
		return EXIT_PREPARE_FAIL;
	}

	oid.install_filter_driver = TRUE;
	printf("Installing filter driver (may prompt for UAC)...\n");
	r = wdi_install_driver(&dev, extract_dir, INF_NAME, &oid);

	if (r != WDI_SUCCESS) {
		fprintf(stderr, "wdi_install_driver failed: %s\n", wdi_strerror(r));
		return EXIT_INSTALL_FAIL;
	}

	printf("Driver installed successfully.\n");
	return EXIT_OK;
}

static int cmd_restore(unsigned short vid, unsigned short pid)
{
	struct device_snap snap;
	struct wdi_device_info dev;
	struct wdi_options_prepare_driver opd = { 0 };
	struct wdi_options_install_driver oid = { 0 };
	char temp_dir[MAX_PATH];
	char extract_dir[MAX_PATH];
	DWORD n;
	int r;

	snap = lookup_device(vid, pid);
	if (!snap.found) {
		fprintf(stderr, "Device %04X:%04X not found.\n",
			(unsigned int)vid, (unsigned int)pid);
		delete_state();
		return EXIT_NOT_FOUND;
	}

	memset(&dev, 0, sizeof(dev));
	dev.vid = vid;
	dev.pid = pid;
	dev.desc = (char *)DEFAULT_NAME;
	dev.hardware_id = snap.hardware_id[0] ? snap.hardware_id : NULL;
	dev.device_id = snap.device_id[0] ? snap.device_id : NULL;
	dev.is_composite = snap.is_composite;
	dev.mi = snap.mi;

	printf("Found device: %s\n",
		snap.hardware_id[0] ? snap.hardware_id : "(no hardware id)");
	printf("Upper filter: %s\n",
		snap.upper_filter[0] ? snap.upper_filter : "(none)");

	/*
	 * Calling wdi_install_driver with install_filter_driver=TRUE on a device
	 * that already has the libusb-win32 filter removes it (libwdi.c).
	 */
	n = GetTempPathA((DWORD)sizeof(temp_dir), temp_dir);
	if (n == 0 || n >= sizeof(temp_dir)) {
		fprintf(stderr, "GetTempPath failed.\n");
		return EXIT_RESTORE_FAIL;
	}
	sprintf_s(extract_dir, sizeof(extract_dir), "%swdi_helper_%04X_%04X",
		temp_dir, (unsigned int)vid, (unsigned int)pid);
	CreateDirectoryA(extract_dir, NULL);

	opd.driver_type = WDI_LIBUSB0;
	opd.vendor_name = "Sony";

	r = wdi_prepare_driver(&dev, extract_dir, INF_NAME, &opd);
	if (r != WDI_SUCCESS) {
		fprintf(stderr, "wdi_prepare_driver failed: %s\n", wdi_strerror(r));
		return EXIT_RESTORE_FAIL;
	}

	oid.install_filter_driver = TRUE;
	printf("Removing libusb-win32 filter (may prompt for UAC)...\n");
	r = wdi_install_driver(&dev, extract_dir, INF_NAME, &oid);

	if (r != WDI_SUCCESS) {
		fprintf(stderr, "Filter removal failed: %s\n", wdi_strerror(r));
		fprintf(stderr, "Falling back to pnputil re-enumeration...\n");
		_flushall();
		system("pnputil /scan-devices >NUL 2>&1");
		delete_state();
		return EXIT_RESTORE_FAIL;
	}

	delete_state();
	printf("Driver restored successfully.\n");
	return EXIT_OK;
}

int main(int argc, char **argv)
{
	const char *cmd = NULL;
	const char *name = DEFAULT_NAME;
	unsigned short vid = DEFAULT_VID;
	unsigned short pid = 0;
	int have_pid = 0;
	int i;

	if (argc < 2) {
		usage();
		return EXIT_USAGE;
	}

	if (strcmp(argv[1], "--help") == 0 || strcmp(argv[1], "-h") == 0) {
		usage();
		return EXIT_OK;
	}

	cmd = argv[1];
	for (i = 2; i < argc; i++) {
		if ((strcmp(argv[i], "--vid") == 0) && (i + 1 < argc)) {
			if (parse_u16(argv[++i], &vid) != 0) {
				fprintf(stderr, "Invalid --vid\n");
				return EXIT_USAGE;
			}
		} else if ((strcmp(argv[i], "--pid") == 0) && (i + 1 < argc)) {
			if (parse_u16(argv[++i], &pid) != 0) {
				fprintf(stderr, "Invalid --pid\n");
				return EXIT_USAGE;
			}
			have_pid = 1;
		} else if ((strcmp(argv[i], "--name") == 0) && (i + 1 < argc)) {
			name = argv[++i];
		} else {
			fprintf(stderr, "Unknown argument: %s\n", argv[i]);
			usage();
			return EXIT_USAGE;
		}
	}

	if (!have_pid) {
		fprintf(stderr, "--pid is required\n");
		usage();
		return EXIT_USAGE;
	}

	wdi_set_log_level(WDI_LOG_LEVEL_WARNING);

	if (strcmp(cmd, "install") == 0) {
		return cmd_install(vid, pid, name);
	}
	if (strcmp(cmd, "restore") == 0) {
		return cmd_restore(vid, pid);
	}

	fprintf(stderr, "Unknown command: %s\n", cmd);
	usage();
	return EXIT_USAGE;
}
